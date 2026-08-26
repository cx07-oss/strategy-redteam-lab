"""Local Ollama adapters for the existing, narrow model-role contracts.

Ollama wording and proposals are not reproducible.  Dataset verification, numeric
scenario application, backtests, failure evaluation, and replay remain deterministic.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.json_schema import GenerateJsonSchema

from strategy_redteam.domain import (
    MAX_CANDIDATES_PER_ROUND,
    AttackBatch,
    Identifier,
    StressScenario,
    _strict_stress_component_output_schema,
)
from strategy_redteam.services import (
    ApplicationBoundaryError,
    AttackerEvidenceSummary,
    DefenderEvidenceSummary,
    DefenderNarrativeBatch,
)

StructuredResponse = TypeVar("StructuredResponse", bound=BaseModel)
_DATE_ONLY_INSTRUCTION = (
    "Date fields named evaluation_start and evaluation_end MUST be calendar dates in exactly "
    'YYYY-MM-DD form. Never emit a time, T00:00:00, timezone, or Z. Valid: "2024-06-15". '
    'Invalid: "2024-06-15T00:00:00Z". The final JSON must conform exactly to the supplied schema.'
)
_CORRECTION_INSTRUCTION = (
    "Your previous response failed strict schema validation. Return a complete replacement JSON "
    "object. evaluation_start and evaluation_end must be YYYY-MM-DD only; timestamps, times, "
    "timezones, and Z suffixes are invalid."
)
_MAX_VALIDATION_DIAGNOSTICS = 5


class OllamaFailureCategory(StrEnum):
    """Bounded safe diagnostics for local-Ollama proposal failures."""

    TRANSPORT = "ollama_transport_failure"
    JSON_OR_SCHEMA = "ollama_json_or_schema_validation_failure"
    CONTEXT = "ollama_context_validation_failure"


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


class _OllamaScenarioPayload(BaseModel):
    """Provider-local model output; request-envelope identity is not model-owned."""

    model_config = ConfigDict(extra="forbid")

    scenarios: tuple[StressScenario, ...] = Field(
        min_length=1,
        max_length=MAX_CANDIDATES_PER_ROUND,
    )
    # Accepted only for compatibility with models that retain the legacy schema. They are ignored.
    schema_version: str | None = None
    experiment_id: Identifier | None = None
    round_number: int | None = None

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: Literal["validation", "serialization"] = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, Any]:
        """Expose the domain's family-discriminated component schema to Ollama only."""
        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict):
            raise TypeError("Ollama scenario schema definitions must be an object")
        definitions["StressComponent"] = _strict_stress_component_output_schema(
            definitions.get("StressComponent")
        )
        return schema


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
        response_validator: Callable[[StructuredResponse], None] | None = None,
    ) -> str:
        schema = response_type.model_json_schema()
        schema_text = json.dumps(schema, ensure_ascii=True, separators=(",", ":"))
        correction_instruction = _CORRECTION_INSTRUCTION
        for correction in (False, True):
            system = f"{instructions}\n\n{_DATE_ONLY_INSTRUCTION}\nSchema: {schema_text}"
            if correction:
                system = f"{system}\n\n{correction_instruction}"
            try:
                response = self._client.chat(
                    model=self._configuration.model,
                    messages=(
                        {"role": "system", "content": system},
                        {"role": "user", "content": evidence.model_dump_json()},
                    ),
                    format=schema,
                    options={"temperature": self._configuration.temperature},
                )
            except Exception as error:
                raise OllamaProviderError(OllamaFailureCategory.TRANSPORT) from error
            try:
                content = _response_content(response)
                json.loads(content)
            except (TypeError, KeyError, ValueError, json.JSONDecodeError) as error:
                if correction:
                    raise OllamaProviderError(
                        OllamaFailureCategory.JSON_OR_SCHEMA
                    ) from error
                continue
            try:
                validated = response_type.model_validate_json(content)
                if response_validator is not None:
                    response_validator(validated)
            except ValidationError as error:
                details = _safe_validation_details(error)
                correction_instruction = _schema_correction_instruction(details)
                if correction:
                    raise OllamaProviderError(
                        OllamaFailureCategory.JSON_OR_SCHEMA,
                        validation_details=details,
                    ) from error
                continue
            except ValueError as error:
                if correction:
                    raise OllamaProviderError(
                        OllamaFailureCategory.JSON_OR_SCHEMA
                    ) from error
                continue
            return validated.model_dump_json()
        raise AssertionError("bounded correction loop must return or raise")


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


def _safe_validation_details(error: ValidationError) -> tuple[str, ...]:
    """Extract bounded schema locations/types without including untrusted invalid values."""
    details: list[str] = []
    for item in error.errors()[:_MAX_VALIDATION_DIAGNOSTICS]:
        location = ".".join(str(part) for part in item["loc"])
        error_type = str(item["type"])
        details.append(f"{location}={error_type}")
    return tuple(details)


def _schema_correction_instruction(details: tuple[str, ...]) -> str:
    """Describe only schema-derived requirements for one complete replacement response."""
    guidance: list[str] = []
    for detail in details:
        location, _, error_type = detail.partition("=")
        if location.endswith(("evaluation_start", "evaluation_end")):
            requirement = "required YYYY-MM-DD date"
        elif error_type == "missing":
            requirement = "required field"
        elif error_type in {"literal_error", "enum"}:
            requirement = "must use an allowed schema value"
        else:
            requirement = "must conform to the supplied schema"
        guidance.append(f"- {location}: {requirement}")
    if not guidance:
        return _CORRECTION_INSTRUCTION
    return (
        "Your response failed strict validation. Return a complete replacement JSON object. "
        "Fix these schema fields:\n" + "\n".join(guidance)
    )


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
        payload = _OllamaScenarioPayload.model_validate_json(
            self._client.run(
            instructions=(
                f"{prompt}\n\n"
                "Generate only the scenario payload. experiment_id and round_number are "
                "application-owned request-envelope fields and are not scenario data. "
                "All evaluation and component dates MUST be observed dates from "
                f"{evidence_summary.market_summary.start_date.isoformat()} through "
                f"{evidence_summary.market_summary.end_date.isoformat()}; allowed symbols are "
                "SPY and TLT, and repeated symbols are invalid."
            ),
            evidence=evidence_summary,
            response_type=_OllamaScenarioPayload,
            )
        )
        return AttackBatch(
            experiment_id=evidence_summary.experiment_id,
            round_number=evidence_summary.round_number,
            scenarios=payload.scenarios,
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
