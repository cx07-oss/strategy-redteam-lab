"""Local Ollama adapters for the existing, narrow model-role contracts.

Ollama wording and proposals are not reproducible.  Dataset verification, numeric
scenario application, backtests, failure evaluation, and replay remain deterministic.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from typing import Protocol, TypeVar, cast

from pydantic import BaseModel, ValidationError

from strategy_redteam.domain import AttackBatch
from strategy_redteam.services import (
    AttackerEvidenceSummary,
    DefenderEvidenceSummary,
    DefenderNarrativeBatch,
)

StructuredResponse = TypeVar("StructuredResponse", bound=BaseModel)


class OllamaConfigurationError(RuntimeError):
    """Ollama-specific configuration is missing or invalid."""


class OllamaProviderError(RuntimeError):
    """A local Ollama request or its structured response failed closed."""


class OllamaChatClient(Protocol):
    """The minimal official-client surface used by these two adapters."""

    def chat(self, **kwargs: object) -> object:
        """Send one bounded, schema-constrained chat request."""


class _OllamaResponseMessage(Protocol):
    content: object


class _OllamaResponse(Protocol):
    message: _OllamaResponseMessage


class OllamaConfiguration:
    """Non-secret settings required solely by the local Ollama provider."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 30.0,
        temperature: float = 0.0,
    ) -> None:
        if not model.strip():
            raise OllamaConfigurationError("STRATEGY_REDTEAM_OLLAMA_MODEL is required")
        if not base_url.startswith(("http://", "https://")):
            raise OllamaConfigurationError("Ollama base URL must be an HTTP(S) URL")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0.0:
            raise OllamaConfigurationError("Ollama timeout must be positive")
        if not math.isfinite(temperature) or not 0.0 <= temperature <= 1.0:
            raise OllamaConfigurationError("Ollama temperature must be in [0, 1]")
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature


def ollama_configuration_from_environment(
    environment: Mapping[str, str] | None = None,
) -> OllamaConfiguration:
    """Read only Ollama settings, never Foundry configuration or credentials."""
    source = os.environ if environment is None else environment
    raw_timeout = source.get("STRATEGY_REDTEAM_OLLAMA_TIMEOUT_SECONDS", "30")
    raw_temperature = source.get("STRATEGY_REDTEAM_OLLAMA_TEMPERATURE", "0")
    try:
        return OllamaConfiguration(
            model=source.get("STRATEGY_REDTEAM_OLLAMA_MODEL", ""),
            base_url=source.get("STRATEGY_REDTEAM_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            timeout_seconds=float(raw_timeout),
            temperature=float(raw_temperature),
        )
    except ValueError as error:
        raise OllamaConfigurationError(
            "Ollama timeout and temperature must be finite numbers"
        ) from error


def _official_client(configuration: OllamaConfiguration) -> OllamaChatClient:
    try:
        from ollama import Client  # type: ignore[import-not-found]
    except ImportError as error:
        raise OllamaConfigurationError(
            "Ollama support requires the optional 'ollama' Python package"
        ) from error
    return cast(
        OllamaChatClient,
        Client(host=configuration.base_url, timeout=configuration.timeout_seconds),
    )


class _OllamaStructuredClient:
    def __init__(
        self,
        configuration: OllamaConfiguration,
        client: OllamaChatClient | None = None,
    ) -> None:
        self._configuration = configuration
        self._client = _official_client(configuration) if client is None else client

    def run(
        self,
        *,
        instructions: str,
        evidence: BaseModel,
        response_type: type[StructuredResponse],
    ) -> str:
        try:
            response = self._client.chat(
                model=self._configuration.model,
                messages=(
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": evidence.model_dump_json()},
                ),
                format=response_type.model_json_schema(),
                options={"temperature": self._configuration.temperature},
            )
        except Exception as error:
            raise OllamaProviderError("Ollama request failed") from error
        try:
            content = _response_content(response)
            # Parse explicitly for malformed-text errors, then validate from JSON so
            # Pydantic preserves JSON's typed date/value decoding under strict mode.
            json.loads(content)
            validated = response_type.model_validate_json(content)
        except (TypeError, KeyError, json.JSONDecodeError, ValidationError) as error:
            raise OllamaProviderError(
                f"Ollama returned invalid {response_type.__name__} structured output"
            ) from error
        return validated.model_dump_json()


def _response_content(response: object) -> str:
    if isinstance(response, Mapping):
        message = response["message"]
        if isinstance(message, Mapping):
            content = message["content"]
        else:
            content = message.content
    else:
        content = cast(_OllamaResponse, response).message.content
    if not isinstance(content, str):
        raise TypeError("Ollama response content was not text")
    return content


class OllamaScenarioProposer:
    """Use a local Ollama model only to return a validated ``AttackBatch``."""

    def __init__(
        self,
        *,
        configuration: OllamaConfiguration,
        client: OllamaChatClient | None = None,
    ) -> None:
        self._client = _OllamaStructuredClient(configuration, client)

    def propose(self, *, prompt: str, evidence_summary: AttackerEvidenceSummary) -> str:
        return self._client.run(
            instructions=prompt,
            evidence=evidence_summary,
            response_type=AttackBatch,
        )


class OllamaReportWriter:
    """Use a local Ollama model only to return validated narrative labels."""

    def __init__(
        self,
        *,
        configuration: OllamaConfiguration,
        client: OllamaChatClient | None = None,
    ) -> None:
        self._client = _OllamaStructuredClient(configuration, client)

    def write(self, *, prompt: str, evidence_summary: DefenderEvidenceSummary) -> str:
        return self._client.run(
            instructions=prompt,
            evidence=evidence_summary,
            response_type=DefenderNarrativeBatch,
        )
