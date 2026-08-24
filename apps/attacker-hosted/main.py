"""Source-deployment entry point for the bounded attacker Hosted Agent."""

from __future__ import annotations

import sys
from pathlib import Path

from azure.ai.agentserver.invocations import InvocationAgentServerHost

source_root = Path(__file__).resolve().parent / "src"
if source_root.is_dir():
    sys.path.insert(0, str(source_root))

from strategy_redteam.foundry_clients import (  # noqa: E402
    FoundryScenarioProposer,
    foundry_configuration,
)
from strategy_redteam.hosted import (  # noqa: E402
    AttackerHostedApplication,
    AttackerHostedRequest,
    AttackerHostedResponse,
    artifact_store_from_environment,
    dataset_store_from_environment,
    packaged_attack_policy,
)
from strategy_redteam.hosted_server import create_invocation_host  # noqa: E402


def build_application() -> AttackerHostedApplication:
    project_endpoint, model = foundry_configuration()
    return AttackerHostedApplication(
        dataset_store=dataset_store_from_environment(),
        artifact_store=artifact_store_from_environment(),
        proposer=FoundryScenarioProposer(
            project_endpoint=project_endpoint,
            model=model,
        ),
        policy=packaged_attack_policy(),
    )


def create_host(
    application: AttackerHostedApplication | None = None,
) -> InvocationAgentServerHost:
    return create_invocation_host(
        title="Strategy Red Team Attacker",
        request_model=AttackerHostedRequest,
        response_model=AttackerHostedResponse,
        application_factory=(lambda: application or build_application()),
        enable_observability=application is None,
    )


if __name__ == "__main__":
    create_host().run()
