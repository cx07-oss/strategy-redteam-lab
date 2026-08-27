"""Local Ollama adapters for the existing, narrow model-role contracts.

Ollama wording and proposals are not reproducible.  Dataset verification, numeric
scenario application, backtests, failure evaluation, and replay remain deterministic.
"""
# ruff: noqa: E501

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, create_model

from strategy_redteam.attack import AttackCatalog
from strategy_redteam.domain import AttackBatch, StressScenario
from strategy_redteam.services import (
    ApplicationBoundaryError,
    AttackerEvidenceSummary,
    DefenderEvidenceSummary,
    DefenderNarrativeBatch,
)

StructuredResponse = TypeVar("StructuredResponse", bound=BaseModel)
_MAX_VALIDATION_DIAGNOSTICS = 5


class OllamaFailureCategory(StrEnum):
    """Bounded safe diagnostics for local-Ollama proposal failures."""

    JSON_OR_SCHEMA = "ollama_json_or_schema_validation_failure"
    CONTEXT = "ollama_context_validation_failure"
    CONNECTION = "ollama_connection_failure"
    TIMEOUT = "ollama_timeout_failure"
    RESPONSE = "ollama_response_error"
    CLIENT = "ollama_client_failure"
    JSON_PARSE = "ollama_json_parse_failure"
    SELECTION_VALIDATION = "ollama_selection_validation_failure"


class OllamaConfigurationError(RuntimeError):
    """Ollama-specific configuration is missing or invalid."""


class OllamaProviderError(ApplicationBoundaryError):
    """A local Ollama request or its structured response failed closed."""

    def __init__(
        self,
        category: OllamaFailureCategory,
        *,
        context_fields: tuple[str, ...] = (),
        validation_details: tuple[str, ...] = (),
    ) -> None:
        detail = category.value
        if context_fields:
            detail = f"{detail}: {','.join(context_fields)}"
        if validation_details:
            detail = f"{detail}: {'; '.join(validation_details)}"
        super().__init__(detail)
        self._safe_rejection_detail = detail

    @property
    def safe_rejection_detail(self) -> str:
        return self._safe_rejection_detail


class _OllamaWirePayload(BaseModel):
    """Accept JSON-mode objects before the strict local selection boundary."""

    model_config = ConfigDict(extra="allow")


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
        from ollama import Client
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
        wire_format: Mapping[str, object] | str | None = None,
        json_parse_category: OllamaFailureCategory = OllamaFailureCategory.JSON_OR_SCHEMA,
        user_content: str | None = None,
    ) -> str:
        format_value = response_type.model_json_schema() if wire_format is None else wire_format
        try:
            response = self._client.chat(
                model=self._configuration.model,
                messages=(
                    {"role": "system", "content": instructions},
                    {
                        "role": "user",
                        "content": evidence.model_dump_json() if user_content is None else user_content,
                    },
                ),
                format=format_value,
                options={"temperature": self._configuration.temperature},
                think=False,
            )
        except Exception as error:
            raise _classify_ollama_client_error(error) from error
        try:
            content = _response_content(response)
            json.loads(content)
        except (TypeError, KeyError, ValueError, json.JSONDecodeError) as error:
            raise OllamaProviderError(json_parse_category) from error
        try:
            return response_type.model_validate_json(content).model_dump_json()
        except ValidationError as error:
            raise OllamaProviderError(
                OllamaFailureCategory.JSON_OR_SCHEMA,
                validation_details=_safe_validation_details(error),
            ) from error


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


def _classify_ollama_client_error(error: Exception) -> OllamaProviderError:
    """Classify only client exception types and safe protocol metadata."""
    import httpx
    from ollama import ResponseError

    if isinstance(error, httpx.ConnectError):
        return OllamaProviderError(OllamaFailureCategory.CONNECTION)
    if isinstance(error, httpx.TimeoutException):
        return OllamaProviderError(
            OllamaFailureCategory.TIMEOUT,
            context_fields=(type(error).__name__,),
        )
    if isinstance(error, ResponseError):
        status = getattr(error, "status_code", None)
        fields = (f"status={status}",) if isinstance(status, int) else ()
        return OllamaProviderError(OllamaFailureCategory.RESPONSE, context_fields=fields)
    return OllamaProviderError(OllamaFailureCategory.CLIENT, context_fields=(type(error).__name__,))


def _safe_validation_details(error: ValidationError) -> tuple[str, ...]:
    """Extract bounded schema locations/types without including untrusted invalid values."""
    details: list[str] = []
    for item in error.errors()[:_MAX_VALIDATION_DIAGNOSTICS]:
        location = ".".join(str(part) for part in item["loc"])
        error_type = str(item["type"])
        details.append(f"{location}={error_type}")
    return tuple(details)


def _catalog_choice_slots(choice_count: int) -> tuple[str, ...]:
    """Return application-owned fixed selection slots for one catalog request."""
    return tuple(f"choice_{index:02d}" for index in range(1, choice_count + 1))


def _catalog_choice_count(
    evidence: AttackerEvidenceSummary, attack_catalog: AttackCatalog
) -> int:
    """Bound choices by both the current application budget and supplied catalog."""
    return min(evidence.max_candidates, evidence.remaining_scenarios, len(attack_catalog.entries))


def _catalog_plaintext(attack_catalog: AttackCatalog, choice_count: int) -> str:
    """Provide finite read-only catalog facts without serializing provider context."""
    entries: list[str] = []
    for entry in attack_catalog.entries:
        scenario = entry.scenario
        families = ", ".join(component.family.value for component in scenario.components)
        entries.append(
            f"{entry.attack_key}: hypothesis={scenario.hypothesis!r}; "
            f"stress_families={families}; components={len(scenario.components)}"
        )
    example = {slot: "atk_NNN" for slot in _catalog_choice_slots(choice_count)}
    return (
        "Select distinct attack keys from this read-only catalog. Do not create, reproduce, "
        "or modify scenarios, values, dates, components, IDs, or parameters.\nCATALOG:\n"
        + "\n".join(entries)
        + "\nReturn JSON only, exactly this fixed shape: "
        + json.dumps(example, separators=(",", ":"))
        + ". Every value must be an existing distinct attack key. No other fields."
    )


def _validate_catalog_selection(
    raw: object, attack_catalog: AttackCatalog, choice_count: int
) -> tuple[str, ...]:
    """Strictly validate exact slots and distinct keys without correction or repair."""
    slots = _catalog_choice_slots(choice_count)
    model = create_model(
        "OllamaCatalogSelection",
        __config__=ConfigDict(extra="forbid"),
        **cast(Any, {slot: (StrictStr, ...) for slot in slots}),
    )
    selection = model.model_validate(raw)
    keys = tuple(cast(str, getattr(selection, slot)) for slot in slots)
    catalog_keys = {entry.attack_key for entry in attack_catalog.entries}
    if len(set(keys)) != len(keys) or any(key not in catalog_keys for key in keys):
        raise ValueError("catalog choices must be unique known attack keys")
    return keys


class OllamaScenarioProposer:
    """Use a local Ollama model only to return a validated ``AttackBatch``."""

    def __init__(
        self,
        *,
        configuration: OllamaConfiguration,
        client: OllamaChatClient | None = None,
    ) -> None:
        self._client = _OllamaStructuredClient(configuration, client)

    def propose(
        self, *, prompt: str, evidence_summary: AttackerEvidenceSummary,
        attack_catalog: AttackCatalog | None = None,
    ) -> str:
        del prompt
        if attack_catalog is None or not attack_catalog.entries:
            raise OllamaProviderError(OllamaFailureCategory.CONTEXT)
        choice_count = _catalog_choice_count(evidence_summary, attack_catalog)
        if choice_count == 0:
            raise OllamaProviderError(OllamaFailureCategory.CONTEXT)
        selection_text = self._client.run(
            instructions="Return only the required catalog-selection JSON.",
            evidence=_OllamaWirePayload(),
            response_type=_OllamaWirePayload,
            wire_format="json",
            json_parse_category=OllamaFailureCategory.JSON_PARSE,
            user_content=_catalog_plaintext(attack_catalog, choice_count),
        )
        try:
            attack_keys = _validate_catalog_selection(
                json.loads(selection_text), attack_catalog, choice_count
            )
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
            details = _safe_validation_details(error) if isinstance(error, ValidationError) else ()
            raise OllamaProviderError(
                OllamaFailureCategory.SELECTION_VALIDATION, validation_details=details
            ) from error
        catalog_by_key = {entry.attack_key: entry.scenario for entry in attack_catalog.entries}
        scenarios = tuple(
            StressScenario.model_validate(
                catalog_by_key[attack_key].model_dump()
                | {"scenario_id": f"ollama-r{evidence_summary.round_number:02d}-c{position:02d}"}
            )
            for position, attack_key in enumerate(attack_keys, start=1)
        )
        return AttackBatch(
            experiment_id=evidence_summary.experiment_id,
            round_number=evidence_summary.round_number,
            scenarios=scenarios,
        ).model_dump_json()


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
