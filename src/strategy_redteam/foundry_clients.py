"""Current Microsoft Agent Framework adapters for the two model-facing roles."""

from __future__ import annotations

import asyncio
import os
from typing import TypeVar

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel

from strategy_redteam.attack import AttackCatalog
from strategy_redteam.domain import AttackBatch
from strategy_redteam.services import (
    AttackerEvidenceSummary,
    DefenderEvidenceSummary,
    DefenderNarrativeBatch,
)

StructuredResponse = TypeVar("StructuredResponse", bound=BaseModel)


class FoundryClientConfigurationError(RuntimeError):
    """Required non-secret Foundry endpoint configuration is missing."""


class _FoundryStructuredClient:
    def __init__(
        self,
        *,
        project_endpoint: str,
        model: str,
        agent_name: str,
        credential: object | None = None,
    ) -> None:
        self.project_endpoint = project_endpoint
        self.model = model
        self.agent_name = agent_name
        self.credential = credential if credential is not None else DefaultAzureCredential()

    async def _run(
        self,
        *,
        instructions: str,
        message: str,
        response_type: type[StructuredResponse],
        seed: int | None = None,
    ) -> str:
        client = FoundryChatClient(
            project_endpoint=self.project_endpoint,
            model=self.model,
            credential=self.credential,
        )
        options: dict[str, object] = {
            "response_format": response_type,
            "store": False,
            "temperature": 0.0,
        }
        if seed is not None:
            options["seed"] = seed
        async with Agent(
            client=client,
            name=self.agent_name,
            instructions=instructions,
            default_options={"store": False},
        ) as agent:
            response = await agent.run(message, options=options)
        if isinstance(response.value, response_type):
            return response.value.model_dump_json()
        return response.text

    def run(
        self,
        *,
        instructions: str,
        message: str,
        response_type: type[StructuredResponse],
        seed: int | None = None,
    ) -> str:
        return asyncio.run(
            self._run(
                instructions=instructions,
                message=message,
                response_type=response_type,
                seed=seed,
            )
        )


class FoundryScenarioProposer:
    """Use FoundryChatClient only to propose typed, bounded AttackBatch JSON."""

    def __init__(
        self,
        *,
        project_endpoint: str,
        model: str,
        credential: object | None = None,
    ) -> None:
        self._client = _FoundryStructuredClient(
            project_endpoint=project_endpoint,
            model=model,
            agent_name="strategy-redteam-attacker",
            credential=credential,
        )

    def propose(
        self,
        *,
        prompt: str,
        evidence_summary: AttackerEvidenceSummary,
        attack_catalog: AttackCatalog | None = None,
    ) -> str:
        del attack_catalog
        return self._client.run(
            instructions=prompt,
            message=evidence_summary.model_dump_json(),
            response_type=AttackBatch,
            seed=evidence_summary.seed,
        )


class FoundryReportWriter:
    """Use FoundryChatClient only for bounded narrative audit labels and reasons."""

    def __init__(
        self,
        *,
        project_endpoint: str,
        model: str,
        credential: object | None = None,
    ) -> None:
        self._client = _FoundryStructuredClient(
            project_endpoint=project_endpoint,
            model=model,
            agent_name="strategy-redteam-defender",
            credential=credential,
        )

    def write(
        self,
        *,
        prompt: str,
        evidence_summary: DefenderEvidenceSummary,
    ) -> str:
        return self._client.run(
            instructions=prompt,
            message=evidence_summary.model_dump_json(),
            response_type=DefenderNarrativeBatch,
        )


def foundry_configuration() -> tuple[str, str]:
    """Read platform-injected endpoint and one non-secret model deployment name."""
    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    if project_endpoint is None or model is None:
        raise FoundryClientConfigurationError(
            "FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME are required"
        )
    return project_endpoint, model
