"""Local Ollama adapters for the existing, narrow model-role contracts.

Ollama wording and proposals are not reproducible.  Dataset verification, numeric
scenario application, backtests, failure evaluation, and replay remain deterministic.
"""
# ruff: noqa: E501

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from pydantic.json_schema import GenerateJsonSchema

from strategy_redteam.attack import (
    AttackCatalog,
    InflationCorrelationHypothesisPolicy,
    RebalanceTimingHypothesisPolicy,
    TradingFrictionHypothesisPolicy,
    VolatilityRegimeHypothesisPolicy,
)
from strategy_redteam.domain import (
    MAX_CANDIDATES_PER_ROUND,
    AttackBatch,
    Identifier,
    StressComponent,
    StressFamily,
    StressScenario,
    Symbol,
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
    CONNECTION = "ollama_connection_failure"
    TIMEOUT = "ollama_timeout_failure"
    RESPONSE = "ollama_response_error"
    CLIENT = "ollama_client_failure"
    JSON_PARSE = "ollama_json_parse_failure"
    STAGE1_VALIDATION = "ollama_stage1_validation_failure"
    STAGE2_VALIDATION = "ollama_stage2_validation_failure"


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


class _TemplateProposalBase(BaseModel):
    """Only model-owned stress choices; canonical structure is built locally."""

    model_config = ConfigDict(extra="forbid")
    hypothesis: str = Field(min_length=1, max_length=2_000)
    headline: str | None = Field(default=None, max_length=280)


class _OllamaWirePayload(BaseModel):
    """Preserve a flat stage-two object for immediate strict local validation."""

    model_config = ConfigDict(extra="allow")


class _Stage1SelectionContext(BaseModel):
    """Non-canonical input for template selection; no provenance or scenario shape."""

    model_config = ConfigDict(extra="forbid")
    allowed_template_keys: tuple[str, ...]
    candidate_slots: tuple[str, ...]
    transaction_cost_bps: float


class _Stage2ParameterContext(BaseModel):
    """Non-canonical input for selected-template parameter generation."""

    model_config = ConfigDict(extra="forbid")
    selected_templates: dict[str, str]
    return_row_keys: tuple[str, ...]
    rebalance_target_keys: tuple[str, ...]


def _template_payload_type(evidence: AttackerEvidenceSummary) -> type[BaseModel]:
    """Build a deterministic, request-specific union of applicable policy rows."""
    rows = tuple(
        row
        for row in evidence.policy.hypotheses
        if not (
            isinstance(row, TradingFrictionHypothesisPolicy)
            and evidence.transaction_cost_bps <= 0.0
        )
    )
    legal_count = len(evidence.return_dates)
    market_positions = {value: index + 1 for index, value in enumerate(evidence.return_dates)}
    targets = tuple(
        value for value in evidence.rebalance_dates if market_positions.get(value, 0) >= 3
    )
    variants: list[type[BaseModel]] = []
    if not rows:
        gap_range = evidence.policy.numeric_ranges.one_day_gap_shock
        variants.append(
            create_model(
                "OllamaGenericGapProposal",
                __base__=_TemplateProposalBase,
                template=(Literal["generic_one_day_gap"], ...),
                return_date_index=(int, Field(strict=True, ge=0, le=legal_count - 1)),
                spy_one_day_gap=(
                    float,
                    Field(strict=True, ge=gap_range.minimum, le=gap_range.maximum),
                ),
            )
        )
    for row in rows:
        common: dict[str, tuple[object, object]] = {
            "template": (Literal[row.hypothesis_family.value], ...),
            "hypothesis": (str, Field(min_length=1, max_length=2_000)),
            "headline": (str | None, Field(default=None, max_length=280)),
        }
        if isinstance(row, InflationCorrelationHypothesisPolicy):
            fields = common | {
                "window_start_index": (int, Field(strict=True, ge=0, le=legal_count - 1)),
                "window_end_index": (int, Field(strict=True, ge=0, le=legal_count - 1)),
                "shock_start_index": (int, Field(strict=True, ge=0, le=legal_count - 1)),
                "duration_rows": (
                    int,
                    Field(
                        strict=True,
                        ge=row.shock_duration_rows.minimum,
                        le=row.shock_duration_rows.maximum,
                    ),
                ),
                "volatility_multiplier": (
                    float,
                    Field(
                        strict=True,
                        ge=row.volatility_multiplier.minimum,
                        le=row.volatility_multiplier.maximum,
                    ),
                ),
                "target_correlation": (
                    float,
                    Field(
                        strict=True,
                        ge=row.target_correlation.minimum,
                        le=row.target_correlation.maximum,
                    ),
                ),
                "spy_cumulative_shock": (
                    float,
                    Field(
                        strict=True,
                        ge=row.spy_cumulative_shock.minimum,
                        le=row.spy_cumulative_shock.maximum,
                    ),
                ),
                "tlt_cumulative_shock": (
                    float,
                    Field(
                        strict=True,
                        ge=row.tlt_cumulative_shock.minimum,
                        le=row.tlt_cumulative_shock.maximum,
                    ),
                ),
            }
        elif isinstance(row, RebalanceTimingHypothesisPolicy):
            fields = common | {
                "rebalance_target_index": (int, Field(strict=True, ge=0, le=len(targets) - 1)),
                "rebalance_offset": (Literal["minus_3", "minus_2", "minus_1"], ...),
                "spy_one_day_gap": (
                    float,
                    Field(
                        strict=True, ge=row.spy_one_day_gap.minimum, le=row.spy_one_day_gap.maximum
                    ),
                ),
                "tlt_one_day_gap": (
                    float,
                    Field(
                        strict=True, ge=row.tlt_one_day_gap.minimum, le=row.tlt_one_day_gap.maximum
                    ),
                ),
            }
        elif isinstance(row, TradingFrictionHypothesisPolicy):
            fields = common | {
                "transaction_cost_multiplier": (
                    float,
                    Field(
                        strict=True,
                        ge=row.transaction_cost_multiplier.minimum,
                        le=row.transaction_cost_multiplier.maximum,
                    ),
                )
            }
        elif isinstance(row, VolatilityRegimeHypothesisPolicy):
            fields = common | {
                "window_start_index": (int, Field(strict=True, ge=0, le=legal_count - 1)),
                "window_end_index": (int, Field(strict=True, ge=0, le=legal_count - 1)),
                "volatility_multiplier": (
                    float,
                    Field(
                        strict=True,
                        ge=row.volatility_multiplier.minimum,
                        le=row.volatility_multiplier.maximum,
                    ),
                ),
            }
        else:
            continue
        variants.append(
            create_model(
                f"Ollama{row.hypothesis_family.value.title().replace('_', '')}Proposal",
                __base__=_TemplateProposalBase,
                **fields,
            )
        )
    if not variants:
        raise OllamaProviderError(OllamaFailureCategory.JSON_OR_SCHEMA)
    union: Any = variants[0]
    for variant in variants[1:]:
        union = union | variant
    return create_model(
        "OllamaTemplatePayload",
        __config__=ConfigDict(extra="forbid"),
        proposals=(
            tuple[union, ...],
            Field(min_length=1, max_length=MAX_CANDIDATES_PER_ROUND),
        ),
    )


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
        response_schema: Mapping[str, object] | None = None,
        response_validator: Callable[[StructuredResponse], None] | None = None,
        max_requests: Literal[1, 2] = 2,
        wire_format: Mapping[str, object] | Literal["json"] | None = None,
        json_parse_category: OllamaFailureCategory = OllamaFailureCategory.JSON_OR_SCHEMA,
        user_content: str | None = None,
    ) -> str:
        schema = response_type.model_json_schema() if response_schema is None else response_schema
        format_value = schema if wire_format is None else wire_format
        schema_text = (
            "" if wire_format == "json" else json.dumps(schema, ensure_ascii=True, separators=(",", ":"))
        )
        correction_instruction = _CORRECTION_INSTRUCTION
        for correction in range(max_requests):
            system = f"{_DATE_ONLY_INSTRUCTION}\n\n{instructions}"
            if schema_text:
                system = f"{system}\nSchema: {schema_text}"
            if correction:
                system = f"{system}\n\n{correction_instruction}"
            try:
                response = self._client.chat(
                    model=self._configuration.model,
                    messages=(
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": evidence.model_dump_json()
                            if user_content is None
                            else user_content,
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
                if correction or max_requests == 1:
                    raise OllamaProviderError(json_parse_category) from error
                continue
            try:
                validated = response_type.model_validate_json(content)
                if response_validator is not None:
                    response_validator(validated)
            except ValidationError as error:
                details = _safe_validation_details(error)
                correction_instruction = _schema_correction_instruction(details)
                if correction or max_requests == 1:
                    raise OllamaProviderError(
                        OllamaFailureCategory.JSON_OR_SCHEMA,
                        validation_details=details,
                    ) from error
                continue
            except ValueError as error:
                if correction or max_requests == 1:
                    raise OllamaProviderError(OllamaFailureCategory.JSON_OR_SCHEMA) from error
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


def _canonical_template_scenario(
    proposal: BaseModel, evidence: AttackerEvidenceSummary, scenario_id: str
) -> StressScenario:
    """Resolve only authoritative calendar/identity fields; preserve model values verbatim."""
    values = proposal.model_dump()
    template = values["template"]
    dates = evidence.return_dates
    start, end = dates[0], dates[-1]
    components: tuple[StressComponent, ...]
    if template == "generic_one_day_gap":
        components = (
            StressComponent(
                family=StressFamily.ONE_DAY_GAP,
                date=dates[values["return_date_index"]],
                shocks={Symbol.SPY: values["spy_one_day_gap"]},
            ),
        )
    elif template == "inflation_correlation_break":
        window_start = dates[values["window_start_index"]]
        window_end = dates[values["window_end_index"]]
        shock_start = dates[values["shock_start_index"]]
        components = (
            StressComponent(
                family=StressFamily.VOLATILITY_MULTIPLIER,
                start_date=window_start,
                end_date=window_end,
                symbols=(Symbol.SPY, Symbol.TLT),
                volatility_multiplier=values["volatility_multiplier"],
            ),
            StressComponent(
                family=StressFamily.CORRELATION_TARGET,
                start_date=window_start,
                end_date=window_end,
                target_correlation=values["target_correlation"],
            ),
            StressComponent(
                family=StressFamily.SUSTAINED_CUMULATIVE_SHOCK,
                start_date=shock_start,
                duration_rows=values["duration_rows"],
                shocks={
                    Symbol.SPY: values["spy_cumulative_shock"],
                    Symbol.TLT: values["tlt_cumulative_shock"],
                },
            ),
        )
    elif template == "rebalance_timing_gap":
        positions = {value: index + 1 for index, value in enumerate(dates)}
        targets = tuple(value for value in evidence.rebalance_dates if positions.get(value, 0) >= 3)
        target = targets[values["rebalance_target_index"]]
        offsets = {"minus_3": -3, "minus_2": -2, "minus_1": -1}
        gap_date = dates[positions[target] - 1 + offsets[values["rebalance_offset"]]]
        components = (
            StressComponent(
                family=StressFamily.ONE_DAY_GAP,
                date=gap_date,
                shocks={
                    Symbol.SPY: values["spy_one_day_gap"],
                    Symbol.TLT: values["tlt_one_day_gap"],
                },
            ),
        )
    elif template == "trading_friction_break":
        components = (
            StressComponent(
                family=StressFamily.TRANSACTION_COST_MULTIPLIER,
                transaction_cost_multiplier=values["transaction_cost_multiplier"],
            ),
        )
    else:
        components = (
            StressComponent(
                family=StressFamily.VOLATILITY_MULTIPLIER,
                start_date=dates[values["window_start_index"]],
                end_date=dates[values["window_end_index"]],
                symbols=(Symbol.SPY, Symbol.TLT),
                volatility_multiplier=values["volatility_multiplier"],
            ),
        )
    return StressScenario(
        scenario_id=scenario_id,
        evaluation_start=start,
        evaluation_end=end,
        components=components,
        hypothesis=values["hypothesis"],
        headline=values["headline"],
    )


def _stage_one_contract(evidence: AttackerEvidenceSummary) -> str:
    """Deterministic textual JSON-mode contract; never an Ollama JSON Schema."""
    slots = _candidate_slots(evidence)
    keys = _template_keys(evidence)
    example = {f"template_{index:02d}": "<allowed template_key>" for index in range(1, len(slots) + 1)}
    return (
        "Return JSON only, exactly matching this object shape: "
        f"{json.dumps(example, separators=(',', ':'))}. "
        f"Allowed template_key values: {', '.join(keys)}. "
        "No markdown, commentary, extra or missing keys, candidate_index, scenario_id, "
        "numeric parameters, dates, rebalance offsets, narratives, or component arrays."
    )


def _stage_two_contract(
    evidence: AttackerEvidenceSummary, template_choices: Mapping[str, str]
) -> str:
    """Describe only exact selected-template JSON fields for local JSON mode."""
    return_keys = [f"row_{index:03d}" for index in range(1, len(evidence.return_dates) + 1)]
    targets = _rebalance_targets(evidence)
    target_keys = [f"rebalance_{index:03d}" for index in range(1, len(targets) + 1)]
    fields = {
        "generic_one_day_gap": ["return_row_key", "spy_one_day_gap"],
        "rebalance_timing_gap": [
            "rebalance_target_key", "rebalance_offset", "spy_one_day_gap", "tlt_one_day_gap",
        ],
        "inflation_correlation_break": [
            "window_start_row_key", "window_end_row_key", "shock_start_row_key",
            "duration_rows", "volatility_multiplier", "target_correlation",
            "spy_cumulative_shock", "tlt_cumulative_shock",
        ],
        "trading_friction_break": ["transaction_cost_multiplier"],
        "volatility_regime_jump": [
            "window_start_row_key", "window_end_row_key", "volatility_multiplier",
        ],
    }
    example: dict[str, object] = {}
    for slot, template in template_choices.items():
        names = ["hypothesis", "headline", *fields[template]]
        example[slot] = {name: _contract_value(name) for name in names}
    return (
        "Return JSON only with exactly these candidate keys and exact fields: "
        f"{json.dumps(example, separators=(',', ':'))}. "
        f"Allowed return_row_key values: {', '.join(return_keys)}. "
        f"Allowed rebalance_target_key values: {', '.join(target_keys)}. "
        "rebalance_offset is exactly one of minus_3, minus_2, minus_1. "
        "Use JSON numbers for numeric fields, a JSON integer for duration_rows, strings for "
        "hypothesis/headline/selectors, and null only for headline. No extra fields, template "
        "keys, scenario IDs, candidate indexes, dates, or component arrays."
    )


def _contract_value(field_name: str) -> object:
    if field_name == "headline":
        return "<string-or-null>"
    if field_name == "hypothesis":
        return "<string>"
    if field_name.endswith("_key") or field_name == "rebalance_offset":
        return f"<{field_name}>"
    return 0 if field_name == "duration_rows" else 0.0


def _candidate_slots(evidence: AttackerEvidenceSummary) -> tuple[str, ...]:
    """Derive bounded orchestration slots from the authoritative attack summary."""
    count = min(evidence.max_candidates, evidence.remaining_scenarios)
    return tuple(f"candidate_{index:02d}" for index in range(1, count + 1))


def _template_keys(evidence: AttackerEvidenceSummary) -> list[str]:
    rows = tuple(
        row
        for row in evidence.policy.hypotheses
        if not (
            isinstance(row, TradingFrictionHypothesisPolicy)
            and evidence.transaction_cost_bps <= 0.0
        )
    )
    return [row.hypothesis_family.value for row in rows] or ["generic_one_day_gap"]


def _rebalance_targets(evidence: AttackerEvidenceSummary) -> tuple[object, ...]:
    positions = {value: index + 1 for index, value in enumerate(evidence.return_dates)}
    return tuple(value for value in evidence.rebalance_dates if positions.get(value, 0) >= 3)


def _stage1_context(evidence: AttackerEvidenceSummary) -> _Stage1SelectionContext:
    return _Stage1SelectionContext(
        allowed_template_keys=tuple(_template_keys(evidence)),
        candidate_slots=_candidate_slots(evidence),
        transaction_cost_bps=evidence.transaction_cost_bps,
    )


def _stage2_context(
    evidence: AttackerEvidenceSummary, template_choices: Mapping[str, str]
) -> _Stage2ParameterContext:
    return _Stage2ParameterContext(
        selected_templates=dict(template_choices),
        return_row_keys=tuple(
            f"row_{index:03d}" for index in range(1, len(evidence.return_dates) + 1)
        ),
        rebalance_target_keys=tuple(
            f"rebalance_{index:03d}" for index, _ in enumerate(_rebalance_targets(evidence), 1)
        ),
    )


def _stage1_plaintext(evidence: AttackerEvidenceSummary) -> str:
    slots = _candidate_slots(evidence)
    templates = "\n".join(f"- {key}" for key in _template_keys(evidence))
    return (
        f"Select attack templates for {len(slots)} fixed positions.\nAllowed templates:\n"
        f"{templates}\nDo not copy labels from this text. Return JSON only.\n"
        f"REQUIRED JSON OUTPUT (and nothing else):\n{_stage_one_contract(evidence)}"
    )


def _stage2_plaintext(
    evidence: AttackerEvidenceSummary, template_choices: Mapping[str, str]
) -> str:
    selected = "\n".join(
        f"Candidate {index} selected template: {template}"
        for index, template in enumerate(template_choices.values(), 1)
    )
    return (
        f"{selected}\nUse only the bounded selectors stated below. Return JSON only.\n"
        f"REQUIRED JSON OUTPUT (and nothing else):\n{_stage_two_contract(evidence, template_choices)}"
    )


def _validate_stage_one(
    raw: object, evidence: AttackerEvidenceSummary
) -> dict[str, str]:
    """Reject extra/missing slots; model owns only one template choice per fixed slot."""
    slots = _candidate_slots(evidence)
    literal = Literal.__getitem__(tuple(_template_keys(evidence)))
    fields: dict[str, object] = {
        f"template_{index:02d}": (literal, ...)
        for index in range(1, len(slots) + 1)
    }
    payload = create_model(
        "OllamaFixedSlots",
        __config__=ConfigDict(extra="forbid"),
        **cast(Any, fields),
    ).model_validate(raw)
    return {
        slot: str(getattr(payload, f"template_{index:02d}"))
        for index, slot in enumerate(slots, 1)
    }


def _resolve_stage_two_candidate(
    candidate: Mapping[str, object], template: str, evidence: AttackerEvidenceSummary
) -> dict[str, object]:
    """Map semantic selectors losslessly to the existing strict local payload fields."""
    values = dict(candidate)
    date_keys = {f"row_{index:03d}": index - 1 for index in range(1, len(evidence.return_dates) + 1)}
    targets = _rebalance_targets(evidence)
    target_keys = {f"rebalance_{index:03d}": index - 1 for index in range(1, len(targets) + 1)}
    selector_fields = {
        "return_row_key": "return_date_index",
        "window_start_row_key": "window_start_index",
        "window_end_row_key": "window_end_index",
        "shock_start_row_key": "shock_start_index",
    }
    for key, destination in selector_fields.items():
        if key in values:
            selected = values.pop(key)
            if not isinstance(selected, str) or selected not in date_keys:
                raise OllamaProviderError(OllamaFailureCategory.JSON_OR_SCHEMA)
            values[destination] = date_keys[selected]
    if "rebalance_target_key" in values:
        selected = values.pop("rebalance_target_key")
        if not isinstance(selected, str) or selected not in target_keys:
            raise OllamaProviderError(OllamaFailureCategory.JSON_OR_SCHEMA)
        values["rebalance_target_index"] = target_keys[selected]
    return {"template": template, **values}


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
        del attack_catalog
        # The policy has already been reduced for the selected strategy.  Remove only rows
        # that the authoritative baseline makes impossible before exposing the contract.
        legal_dates = evidence_summary.return_dates
        if not legal_dates:
            raise OllamaProviderError(OllamaFailureCategory.JSON_OR_SCHEMA)
        stage_one_text = self._client.run(
            instructions=(
                f"{prompt}\n\n{_stage_one_contract(evidence_summary)}"
            ),
            evidence=_stage1_context(evidence_summary),
            response_type=_OllamaWirePayload,
            max_requests=1,
            wire_format="json",
            json_parse_category=OllamaFailureCategory.JSON_PARSE,
            user_content=_stage1_plaintext(evidence_summary),
        )
        try:
            template_choices = _validate_stage_one(
                json.loads(stage_one_text), evidence_summary
            )
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
            details = _safe_validation_details(error) if isinstance(error, ValidationError) else ()
            raise OllamaProviderError(
                OllamaFailureCategory.STAGE1_VALIDATION, validation_details=details
            ) from error
        stage_two_text = self._client.run(
            instructions=(
                _stage_two_contract(evidence_summary, template_choices)
            ),
            evidence=_stage2_context(evidence_summary, template_choices),
            response_type=_OllamaWirePayload,
            max_requests=1,
            wire_format="json",
            json_parse_category=OllamaFailureCategory.JSON_PARSE,
            user_content=_stage2_plaintext(evidence_summary, template_choices),
        )
        try:
            stage_two = json.loads(stage_two_text)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise OllamaProviderError(OllamaFailureCategory.STAGE2_VALIDATION) from error
        if not isinstance(stage_two, dict) or set(stage_two) != set(template_choices):
            raise OllamaProviderError(OllamaFailureCategory.STAGE2_VALIDATION)
        combined: dict[str, list[dict[str, object]]] = {"proposals": []}
        for slot, template in template_choices.items():
            candidate = stage_two.get(slot)
            if not isinstance(candidate, dict):
                raise OllamaProviderError(OllamaFailureCategory.STAGE2_VALIDATION)
            try:
                combined["proposals"].append(
                    _resolve_stage_two_candidate(candidate, template, evidence_summary)
                )
            except OllamaProviderError as error:
                raise OllamaProviderError(OllamaFailureCategory.STAGE2_VALIDATION) from error
        payload_type = _template_payload_type(evidence_summary)
        try:
            payload = payload_type.model_validate(combined)
        except ValidationError as error:
            raise OllamaProviderError(
                OllamaFailureCategory.STAGE2_VALIDATION,
                validation_details=_safe_validation_details(error),
            ) from error
        scenarios = tuple(
            _canonical_template_scenario(
                proposal,
                evidence_summary,
                f"ollama-r{evidence_summary.round_number:02d}-c{position:02d}",
            )
            for position, proposal in enumerate(cast(Any, payload).proposals, start=1)
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
