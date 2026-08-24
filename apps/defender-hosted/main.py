"""Source-deployment entry point for the independent defender Hosted Agent."""

from __future__ import annotations

import sys
from pathlib import Path

from azure.ai.agentserver.invocations import InvocationAgentServerHost

source_root = Path(__file__).resolve().parent / "src"
if source_root.is_dir():
    sys.path.insert(0, str(source_root))

from strategy_redteam.foundry_clients import (  # noqa: E402
    FoundryReportWriter,
    foundry_configuration,
)
from strategy_redteam.hosted import (  # noqa: E402
    DefenderHostedApplication,
    DefenderHostedRequest,
    DefenderHostedResponse,
    artifact_store_from_environment,
    dataset_store_from_environment,
)
from strategy_redteam.hosted_server import create_invocation_host  # noqa: E402


def build_application() -> DefenderHostedApplication:
    project_endpoint, model = foundry_configuration()
    return DefenderHostedApplication(
        dataset_store=dataset_store_from_environment(),
        artifact_store=artifact_store_from_environment(),
        report_writer=FoundryReportWriter(
            project_endpoint=project_endpoint,
            model=model,
        ),
    )


def create_host(
    application: DefenderHostedApplication | None = None,
) -> InvocationAgentServerHost:
    return create_invocation_host(
        title="Strategy Red Team Defender",
        request_model=DefenderHostedRequest,
        response_model=DefenderHostedResponse,
        application_factory=(lambda: application or build_application()),
        enable_observability=application is None,
    )


if __name__ == "__main__":
    create_host().run()
