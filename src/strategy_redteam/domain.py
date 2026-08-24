"""Versioned, cloud-agnostic domain contracts.

Narrative fields in these models are inert metadata. Only validated numeric fields
describe stress operations; this module never interprets narrative text as code,
paths, URLs, commands, or tool instructions.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from pydantic.json_schema import (
    DEFAULT_REF_TEMPLATE,
    GenerateJsonSchema,
    JsonSchemaMode,
    JsonSchemaValue,
)

MAX_ROUNDS = 3
MAX_CANDIDATES_PER_ROUND = 8
MAX_TOTAL_SCENARIOS = 24
TOP_K = 3
DEFAULT_NUMERIC_TOLERANCE = 1e-9
MAX_COMPONENTS_PER_SCENARIO = 8
MAX_STRESS_DURATION_ROWS = 252
ROLLING_WINDOW_ROWS = 20
HISTORICAL_WINDOW_ROWS: tuple[Literal[20], Literal[60], Literal[126]] = (20, 60, 126)

SchemaVersion = Literal["1.0"]
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]
ScenarioIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
NarrativeText = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DateValue = Annotated[date, Field(strict=True)]
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
PositiveFloat = Annotated[float, Field(strict=True, gt=0.0, allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(strict=True, ge=0.0, allow_inf_nan=False)]
UnitFloat = Annotated[float, Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)]
TransactionCostBps = Annotated[
    float,
    Field(strict=True, ge=0.0, lt=10_000.0, allow_inf_nan=False),
]
LossBoundedFloat = Annotated[float, Field(strict=True, gt=-1.0, allow_inf_nan=False)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
CorrelationFloat = Annotated[
    float,
    Field(strict=True, ge=-1.0, le=1.0, allow_inf_nan=False),
]


class ContractModel(BaseModel):
    """Shared validation policy for all boundary models."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class Symbol(StrEnum):
    """Symbols supported by the frozen MVP."""

    SPY = "SPY"
    TLT = "TLT"


def _nullable_symbol_shocks_schema() -> JsonSchemaValue:
    """Describe a partial symbol map without unsupported dictionary keywords."""
    symbols = tuple(symbol.value for symbol in Symbol)
    return {
        "type": "object",
        "properties": {
            symbol: {
                "anyOf": [
                    {"type": "number"},
                    {"type": "null"},
                ]
            }
            for symbol in symbols
        },
        "required": list(symbols),
        "additionalProperties": False,
    }


ShockDictionary: TypeAlias = Annotated[
    dict[Symbol, LossBoundedFloat],
    WithJsonSchema(_nullable_symbol_shocks_schema()),
]


_MODEL_OUTPUT_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "description",
        "enum",
        "items",
        "properties",
        "required",
        "title",
        "type",
    }
)


def _strict_model_output_schema(value: object) -> object:
    """Return the Azure/OpenAI strict subset without weakening runtime models."""
    if isinstance(value, list):
        return [_strict_model_output_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    strict: dict[str, object] = {}
    for key, item in value.items():
        if key in {"$defs", "properties"} and isinstance(item, dict):
            strict[key] = {
                name: _strict_model_output_schema(schema)
                for name, schema in item.items()
            }
        elif key == "const":
            strict["enum"] = [item]
        elif key in _MODEL_OUTPUT_SCHEMA_KEYWORDS:
            strict[key] = _strict_model_output_schema(item)

    if strict.get("type") == "object":
        properties = strict.get("properties", {})
        if not isinstance(properties, dict):
            raise TypeError("object schema properties must be a dictionary")
        strict["properties"] = properties
        strict["required"] = list(properties)
        strict["additionalProperties"] = False
    return strict


class AdjustmentPolicy(StrEnum):
    """Known adjusted-price policies."""

    SPLITS_AND_DISTRIBUTIONS = "splits_and_distributions"


class StrategyKind(StrEnum):
    """Supported strategy sources."""

    MONTHLY_60_40 = "monthly_60_40"
    EXTERNAL_WEIGHTS = "external_weights"


class RebalanceFrequency(StrEnum):
    """Supported decision schedules."""

    MONTH_START = "month_start"
    EXTERNAL = "external"


class FailureRuleFamily(StrEnum):
    """Deterministic failure rules from the specification."""

    MAXIMUM_DRAWDOWN = "maximum_drawdown"
    ROLLING_20_DAY_LOSS = "rolling_20_day_loss"
    REALIZED_VOLATILITY_MULTIPLE = "realized_volatility_multiple"


class StressFamily(StrEnum):
    """Numeric stress operations supported by the engine contract."""

    HISTORICAL_WINDOW = "historical_window"
    ONE_DAY_GAP = "one_day_gap"
    SUSTAINED_CUMULATIVE_SHOCK = "sustained_cumulative_shock"
    VOLATILITY_MULTIPLIER = "volatility_multiplier"
    CORRELATION_TARGET = "correlation_target"
    TRANSACTION_COST_MULTIPLIER = "transaction_cost_multiplier"


_STRESS_COMPONENT_FIELDS: dict[StressFamily, tuple[str, ...]] = {
    StressFamily.HISTORICAL_WINDOW: ("start_date", "end_date"),
    StressFamily.ONE_DAY_GAP: ("date", "shocks"),
    StressFamily.SUSTAINED_CUMULATIVE_SHOCK: (
        "start_date",
        "duration_rows",
        "shocks",
    ),
    StressFamily.VOLATILITY_MULTIPLIER: (
        "start_date",
        "end_date",
        "symbols",
        "volatility_multiplier",
    ),
    StressFamily.CORRELATION_TARGET: (
        "start_date",
        "end_date",
        "target_correlation",
    ),
    StressFamily.TRANSACTION_COST_MULTIPLIER: ("transaction_cost_multiplier",),
}
_STRESS_COMPONENT_NUMERIC_FIELDS = frozenset(
    field_name
    for family_fields in _STRESS_COMPONENT_FIELDS.values()
    for field_name in family_fields
)


def _required_output_field_schema(value: object) -> object:
    """Remove only the runtime model's outer optional-null branch."""
    if not isinstance(value, dict):
        raise TypeError("component field schema must be an object")
    options = value.get("anyOf")
    if not isinstance(options, list):
        return value
    non_null = [
        option
        for option in options
        if not isinstance(option, dict) or option.get("type") != "null"
    ]
    if len(non_null) == 1 and len(non_null) != len(options):
        return non_null[0]
    if len(non_null) != len(options):
        return {**value, "anyOf": non_null}
    return value


def _strict_stress_component_output_schema(value: object) -> JsonSchemaValue:
    """Discriminate every runtime family without changing runtime validation."""
    if not isinstance(value, dict):
        raise TypeError("StressComponent schema must be an object")
    properties = value.get("properties")
    if not isinstance(properties, dict):
        raise TypeError("StressComponent schema properties must be a dictionary")

    variants: list[JsonSchemaValue] = []
    for family, field_names in _STRESS_COMPONENT_FIELDS.items():
        variant_properties: dict[str, object] = {
            "schema_version": properties["schema_version"],
            "family": {"type": "string", "enum": [family.value]},
        }
        variant_properties.update(
            {
                field_name: _required_output_field_schema(properties[field_name])
                for field_name in field_names
            }
        )
        variants.append(
            {
                "type": "object",
                "properties": variant_properties,
                "required": list(variant_properties),
                "additionalProperties": False,
            }
        )
    return {
        "title": value.get("title", "StressComponent"),
        "description": value.get("description", "One numeric stress operation."),
        "anyOf": variants,
    }


class ResultStatus(StrEnum):
    """Whether the deterministic engine accepted a scenario."""

    VALID = "valid"
    REJECTED = "rejected"


class RejectionCode(StrEnum):
    """Stable rejection categories emitted at the typed boundary."""

    INVALID_PARAMETER = "invalid_parameter"
    UNSUPPORTED_SYMBOL = "unsupported_symbol"
    INVALID_WINDOW = "invalid_window"
    BUDGET_EXCEEDED = "budget_exceeded"
    DUPLICATE_SCENARIO = "duplicate_scenario"
    INVALID_DATA = "invalid_data"
    TIMEOUT = "timeout"


class DefenderVerdictValue(StrEnum):
    """Outcomes permitted for independent defence replay."""

    REPRODUCED = "reproduced"
    NOT_REPRODUCED = "not_reproduced"
    INVALID_EVIDENCE = "invalid_evidence"


class DataManifest(ContractModel):
    """Immutable dataset provenance, excluding the manifest's self-hash."""

    schema_version: SchemaVersion = "1.0"
    dataset_id: Identifier
    provider: ShortText
    source_identifiers: dict[Symbol, Identifier]
    symbols: tuple[Symbol, ...] = Field(min_length=2, max_length=2)
    requested_start_date: DateValue
    requested_end_date: DateValue
    start_date: DateValue
    end_date: DateValue
    adjustment_policy: AdjustmentPolicy
    calendar_policy: ShortText
    missing_data_policy: Literal["reject"]
    row_count: PositiveInt
    columns: tuple[Identifier, ...] = Field(min_length=3)
    retrieved_at: datetime
    media_type: Literal["application/vnd.apache.parquet"]
    byte_length: PositiveInt
    sha256: Sha256

    @field_validator("retrieved_at")
    @classmethod
    def retrieval_must_be_utc(cls, value: datetime) -> datetime:
        """Require an explicit UTC timestamp without silently normalizing it."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("retrieved_at must be timezone-aware UTC")
        return value

    @model_validator(mode="after")
    def validate_manifest_contract(self) -> Self:
        """Validate ordering, uniqueness, and the fixed symbol contract."""
        if self.requested_start_date > self.requested_end_date:
            raise ValueError("requested_start_date must be on or before requested_end_date")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        if self.start_date < self.requested_start_date or self.end_date > self.requested_end_date:
            raise ValueError("actual dates must be inside the requested period")
        if self.symbols != (Symbol.SPY, Symbol.TLT):
            raise ValueError("symbols must be ordered as SPY, TLT")
        if set(self.source_identifiers) != set(self.symbols):
            raise ValueError("source_identifiers must exactly match symbols")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("columns must be unique")
        return self


class StrategySpec(ContractModel):
    """Strategy choice and the one-row execution timing convention."""

    schema_version: SchemaVersion = "1.0"
    strategy_id: Identifier
    kind: StrategyKind
    symbols: tuple[Symbol, ...] = Field(min_length=2, max_length=2)
    target_weights: dict[Symbol, UnitFloat] | None
    rebalance_frequency: RebalanceFrequency
    execution_lag_rows: Literal[1] = 1
    allow_short_exposure: StrictBool = False
    allow_leverage: StrictBool = False
    allow_missing_weights: StrictBool = False

    @model_validator(mode="after")
    def validate_strategy_contract(self) -> Self:
        """Keep built-in and external-weight configuration unambiguous."""
        if self.symbols != (Symbol.SPY, Symbol.TLT):
            raise ValueError("symbols must be ordered as SPY, TLT")

        if self.kind is StrategyKind.MONTHLY_60_40:
            expected = {Symbol.SPY: 0.6, Symbol.TLT: 0.4}
            if self.rebalance_frequency is not RebalanceFrequency.MONTH_START:
                raise ValueError("monthly_60_40 must rebalance at the first monthly close")
            if self.target_weights is None or set(self.target_weights) != set(expected):
                raise ValueError("monthly_60_40 requires SPY and TLT target weights")
            if any(
                not math.isclose(self.target_weights[symbol], weight, abs_tol=1e-12)
                for symbol, weight in expected.items()
            ):
                raise ValueError("monthly_60_40 weights must be exactly 0.60 and 0.40")
            if self.allow_short_exposure or self.allow_leverage or self.allow_missing_weights:
                raise ValueError("monthly_60_40 does not permit relaxed weight validation")
        else:
            if self.rebalance_frequency is not RebalanceFrequency.EXTERNAL:
                raise ValueError("external_weights must use the external schedule")
            if self.target_weights is not None:
                raise ValueError("external_weights cannot declare static target weights")
        return self


class FailureRule(ContractModel):
    """One positive failure threshold with fixed rolling-window semantics."""

    schema_version: SchemaVersion = "1.0"
    rule_id: Identifier
    family: FailureRuleFamily
    threshold: PositiveFloat
    window_rows: PositiveInt | None

    @model_validator(mode="after")
    def validate_rule_contract(self) -> Self:
        """Reject impossible loss limits and non-standard rolling windows."""
        is_loss_rule = self.family in {
            FailureRuleFamily.MAXIMUM_DRAWDOWN,
            FailureRuleFamily.ROLLING_20_DAY_LOSS,
        }
        if is_loss_rule and self.threshold >= 1.0:
            raise ValueError("loss thresholds must be strictly less than 1")

        if self.family is FailureRuleFamily.MAXIMUM_DRAWDOWN:
            if self.window_rows is not None:
                raise ValueError("maximum_drawdown does not accept window_rows")
        elif self.window_rows != ROLLING_WINDOW_ROWS:
            raise ValueError("rolling rules require exactly 20 rows")
        return self


class ExperimentSpec(ContractModel):
    """Serializable experiment configuration with hard execution budgets."""

    schema_version: SchemaVersion = "1.0"
    experiment_id: Identifier
    dataset_id: Identifier
    data_sha256: Sha256
    strategy: StrategySpec
    failure_rules: tuple[FailureRule, ...] = Field(min_length=1, max_length=3)
    seed: Annotated[int, Field(strict=True, ge=0, le=4_294_967_295)]
    timeout_seconds: PositiveFloat
    code_version: Identifier
    numeric_tolerance: Annotated[
        float,
        Field(strict=True, gt=0.0, lt=1.0, allow_inf_nan=False),
    ]
    transaction_cost_bps: TransactionCostBps = 0.0
    historical_window_rows: tuple[Literal[20], Literal[60], Literal[126]] = (
        HISTORICAL_WINDOW_ROWS
    )
    max_rounds: Annotated[int, Field(strict=True, ge=1, le=MAX_ROUNDS)] = MAX_ROUNDS
    max_candidates_per_round: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_CANDIDATES_PER_ROUND),
    ] = MAX_CANDIDATES_PER_ROUND
    max_total_scenarios: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_TOTAL_SCENARIOS),
    ] = MAX_TOTAL_SCENARIOS
    top_k: Annotated[int, Field(strict=True, ge=1, le=TOP_K)] = TOP_K

    @model_validator(mode="after")
    def validate_experiment_contract(self) -> Self:
        """Reject duplicate rules and internally inconsistent budgets."""
        rule_ids = [rule.rule_id for rule in self.failure_rules]
        rule_families = [rule.family for rule in self.failure_rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("failure rule IDs must be unique")
        if len(set(rule_families)) != len(rule_families):
            raise ValueError("failure rule families must be unique")
        capacity = self.max_rounds * self.max_candidates_per_round
        if self.max_total_scenarios > capacity:
            raise ValueError("max_total_scenarios exceeds round capacity")
        if self.top_k > self.max_total_scenarios:
            raise ValueError("top_k exceeds max_total_scenarios")
        return self


class StressComponent(ContractModel):
    """One discriminated numeric stress operation.

    Fields not defined for the selected family are rejected so no hidden numeric
    meaning can be smuggled through an ambiguous payload.
    """

    schema_version: SchemaVersion = "1.0"
    family: StressFamily
    start_date: DateValue | None = None
    end_date: DateValue | None = None
    date: DateValue | None = None
    duration_rows: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_STRESS_DURATION_ROWS),
    ] | None = None
    shocks: ShockDictionary | None = Field(default=None, min_length=1, max_length=2)
    symbols: tuple[Symbol, ...] | None = Field(default=None, min_length=1, max_length=2)
    volatility_multiplier: PositiveFloat | None = None
    target_correlation: Annotated[
        float,
        Field(strict=True, gt=-1.0, lt=1.0, allow_inf_nan=False),
    ] | None = None
    transaction_cost_multiplier: PositiveFloat | None = None

    @field_validator("shocks", mode="before")
    @classmethod
    def omit_nullable_symbol_shocks(cls, value: object) -> object:
        """Convert model-facing nulls to omitted known symbols before validation."""
        if not isinstance(value, Mapping):
            return value
        allowed = frozenset(symbol.value for symbol in Symbol)
        keys = tuple(key.value if isinstance(key, Symbol) else key for key in value)
        if any(key not in allowed for key in keys):
            return value
        return {key: shock for key, shock in value.items() if shock is not None}

    @model_validator(mode="after")
    def validate_component_contract(self) -> Self:
        """Require exactly the numeric fields defined for the chosen family."""
        required = frozenset(_STRESS_COMPONENT_FIELDS[self.family])
        provided = {
            name
            for name in _STRESS_COMPONENT_NUMERIC_FIELDS
            if getattr(self, name) is not None
        }
        missing = sorted(required - provided)
        unexpected = sorted(provided - required)
        if missing:
            raise ValueError(f"missing fields for {self.family.value}: {', '.join(missing)}")
        if unexpected:
            raise ValueError(f"unexpected fields for {self.family.value}: {', '.join(unexpected)}")
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError("start_date must be on or before end_date")
        if self.symbols is not None and len(set(self.symbols)) != len(self.symbols):
            raise ValueError("component symbols must be unique")
        return self


class AssetReturnSummary(ContractModel):
    """Deterministic log-return statistics for one asset and window."""

    schema_version: SchemaVersion = "1.0"
    symbol: Symbol
    cumulative_simple_return: LossBoundedFloat
    mean_log_return: FiniteFloat
    sample_log_return_std: NonNegativeFloat | None


class ReturnSummary(ContractModel):
    """Ordered multi-asset statistics retained around a stress transform."""

    schema_version: SchemaVersion = "1.0"
    start_date: DateValue
    end_date: DateValue
    row_count: PositiveInt
    assets: tuple[AssetReturnSummary, ...] = Field(min_length=2, max_length=2)
    spy_tlt_correlation: CorrelationFloat | None

    @model_validator(mode="after")
    def validate_summary_contract(self) -> Self:
        """Keep the fixed MVP assets unique while retaining source column order."""
        symbols = tuple(asset.symbol for asset in self.assets)
        if set(symbols) != {Symbol.SPY, Symbol.TLT}:
            raise ValueError("return summaries require exactly SPY and TLT")
        if self.start_date > self.end_date:
            raise ValueError("summary start_date must be on or before end_date")
        return self


class ComponentTransformSummary(ContractModel):
    """Matching pre/post statistics for one resolved component window."""

    schema_version: SchemaVersion = "1.0"
    component_index: Annotated[
        int,
        Field(strict=True, ge=0, lt=MAX_COMPONENTS_PER_SCENARIO),
    ]
    family: StressFamily
    pre_transform_summary: ReturnSummary
    post_transform_summary: ReturnSummary
    transaction_cost_bps_before: TransactionCostBps
    transaction_cost_bps_after: TransactionCostBps

    @model_validator(mode="after")
    def summaries_must_use_identical_windows(self) -> Self:
        """Prevent a before/after comparison across different dates or assets."""
        before = self.pre_transform_summary
        after = self.post_transform_summary
        before_symbols = tuple(asset.symbol for asset in before.assets)
        after_symbols = tuple(asset.symbol for asset in after.assets)
        if (
            before.start_date != after.start_date
            or before.end_date != after.end_date
            or before.row_count != after.row_count
            or before_symbols != after_symbols
        ):
            raise ValueError("component pre/post summaries must use an identical window")
        return self


class StressScenario(ContractModel):
    """An evaluation window, bounded numeric components, and inert narrative."""

    schema_version: SchemaVersion = "1.0"
    scenario_id: ScenarioIdentifier
    evaluation_start: DateValue
    evaluation_end: DateValue
    components: tuple[StressComponent, ...] = Field(
        min_length=1,
        max_length=MAX_COMPONENTS_PER_SCENARIO,
    )
    hypothesis: NarrativeText
    headline: ShortText | None = None

    @model_validator(mode="after")
    def validate_scenario_contract(self) -> Self:
        """Keep component dates inside the declared window and payloads unique."""
        if self.evaluation_start > self.evaluation_end:
            raise ValueError("evaluation_start must be on or before evaluation_end")

        component_payloads: list[str] = []
        for component in self.components:
            dates = (value for value in (component.start_date, component.end_date, component.date))
            if any(
                value < self.evaluation_start or value > self.evaluation_end
                for value in dates
                if value is not None
            ):
                raise ValueError("component dates must fall inside the evaluation window")
            component_payloads.append(component.model_dump_json(exclude_none=True))
        if len(set(component_payloads)) != len(component_payloads):
            raise ValueError("scenario components must be unique")
        return self


class AttackBatch(ContractModel):
    """One bounded round of scenario proposals."""

    schema_version: SchemaVersion = "1.0"
    experiment_id: Identifier
    round_number: Annotated[int, Field(strict=True, ge=1, le=MAX_ROUNDS)]
    scenarios: tuple[StressScenario, ...] = Field(
        min_length=1,
        max_length=MAX_CANDIDATES_PER_ROUND,
    )

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = DEFAULT_REF_TEMPLATE,
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: JsonSchemaMode = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> JsonSchemaValue:
        """Expose only the strict structured-output subset to model clients."""
        generated = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )
        schema = _strict_model_output_schema(generated)
        if not isinstance(schema, dict):
            raise TypeError("AttackBatch JSON schema must be an object")
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict):
            raise TypeError("AttackBatch JSON schema definitions must be an object")
        definitions["StressComponent"] = _strict_stress_component_output_schema(
            definitions.get("StressComponent")
        )
        return schema

    @model_validator(mode="after")
    def scenario_ids_must_be_unique(self) -> Self:
        """Reject duplicate stable identifiers within a proposal round."""
        scenario_ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("scenario IDs must be unique within a batch")
        return self


class MetricSet(ContractModel):
    """Deterministic portfolio metrics; losses cannot exceed total capital."""

    schema_version: SchemaVersion = "1.0"
    total_return: LossBoundedFloat
    maximum_drawdown: Annotated[
        float,
        Field(strict=True, ge=0.0, lt=1.0, allow_inf_nan=False),
    ]
    worst_rolling_20_day_return: LossBoundedFloat
    annualized_volatility: NonNegativeFloat
    observation_count: PositiveInt


class FailureBreach(ContractModel):
    """A breached rule expressed as an adverse positive magnitude."""

    schema_version: SchemaVersion = "1.0"
    rule_id: Identifier
    family: FailureRuleFamily
    observed_value: PositiveFloat
    threshold: PositiveFloat
    normalized_excess: PositiveFloat
    onset_date: DateValue
    worst_window_start: DateValue
    worst_window_end: DateValue
    trough_date: DateValue | None = None
    recovery_date: DateValue | None = None
    affected_symbols: tuple[Symbol, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_breach_contract(self) -> Self:
        """Reject non-breaches and invalid or duplicate affected positions."""
        if self.observed_value <= self.threshold:
            raise ValueError("observed_value must exceed threshold")
        if self.worst_window_start > self.worst_window_end:
            raise ValueError("worst_window_start must be on or before worst_window_end")
        if self.trough_date is not None:
            if self.family is not FailureRuleFamily.MAXIMUM_DRAWDOWN:
                raise ValueError("trough_date is only valid for maximum_drawdown")
            if self.trough_date != self.worst_window_end:
                raise ValueError("drawdown trough_date must equal worst_window_end")
        if self.recovery_date is not None and self.recovery_date < self.worst_window_end:
            raise ValueError("recovery_date cannot precede worst_window_end")
        if len(set(self.affected_symbols)) != len(self.affected_symbols):
            raise ValueError("affected_symbols must be unique")
        return self


class HistoricalWindowEvidence(ContractModel):
    """Engine-owned characterization of one selected historical failure window."""

    schema_version: SchemaVersion = "1.0"
    window_rows: Literal[20, 60, 126]
    start_date: DateValue
    end_date: DateValue
    breach_onset_date: DateValue
    loss_start_date: DateValue
    trough_date: DateValue
    recovery_date: DateValue | None
    asset_returns: dict[Symbol, LossBoundedFloat]
    asset_realized_volatilities: dict[Symbol, NonNegativeFloat]
    spy_tlt_correlation: CorrelationFloat | None
    total_turnover: NonNegativeFloat
    total_transaction_cost: NonNegativeFloat
    portfolio_loss_to_trough: Annotated[
        float,
        Field(strict=True, ge=0.0, lt=1.0, allow_inf_nan=False),
    ]
    asset_loss_contributions: dict[Symbol, FiniteFloat]
    transaction_cost_loss_contribution: NonNegativeFloat

    @model_validator(mode="after")
    def validate_historical_window_evidence(self) -> Self:
        """Require complete symbols, ordered dates, and reconciled loss attribution."""
        expected_symbols = {Symbol.SPY, Symbol.TLT}
        symbol_maps = (
            self.asset_returns,
            self.asset_realized_volatilities,
            self.asset_loss_contributions,
        )
        if any(set(values) != expected_symbols for values in symbol_maps):
            raise ValueError("historical window symbol maps must exactly contain SPY and TLT")
        if self.start_date > self.end_date:
            raise ValueError("historical window start_date must be on or before end_date")
        dated_events = (
            self.breach_onset_date,
            self.loss_start_date,
            self.trough_date,
        )
        if any(value < self.start_date or value > self.end_date for value in dated_events):
            raise ValueError("historical window events must fall inside the selected window")
        if self.loss_start_date > self.trough_date:
            raise ValueError("loss_start_date must be on or before trough_date")
        if self.recovery_date is not None and self.recovery_date < self.trough_date:
            raise ValueError("recovery_date cannot precede trough_date")
        attributed_loss = math.fsum(self.asset_loss_contributions.values())
        attributed_loss += self.transaction_cost_loss_contribution
        if not math.isclose(attributed_loss, self.portfolio_loss_to_trough, abs_tol=1e-10):
            raise ValueError("loss contributions must reconcile to portfolio_loss_to_trough")
        return self


class StressResult(ContractModel):
    """Typed engine evidence or a typed rejection, never agent-calculated data."""

    schema_version: SchemaVersion = "1.0"
    experiment_id: Identifier
    scenario_id: ScenarioIdentifier
    dataset_id: Identifier
    strategy_id: Identifier
    input_sha256: Sha256
    config_sha256: Sha256
    data_sha256: Sha256
    code_version: Identifier
    engine_version: Identifier
    status: ResultStatus
    metrics: MetricSet | None = None
    historical_window: HistoricalWindowEvidence | None = None
    breaches: tuple[FailureBreach, ...] = Field(default=(), max_length=3)
    breach_count: Annotated[int, Field(strict=True, ge=0, le=3)] = 0
    maximum_normalized_excess: NonNegativeFloat = 0.0
    total_normalized_excess: NonNegativeFloat = 0.0
    worst_portfolio_loss: Annotated[
        float,
        Field(strict=True, ge=0.0, lt=1.0, allow_inf_nan=False),
    ] = 0.0
    rank: PositiveInt | None = None
    rejection_code: RejectionCode | None = None
    rejection_detail: NarrativeText | None = None

    @model_validator(mode="after")
    def validate_result_contract(self) -> Self:
        """Keep valid evidence and rejected proposals mutually exclusive."""
        if self.status is ResultStatus.VALID:
            if self.metrics is None:
                raise ValueError("valid results require metrics")
            if self.rejection_code is not None or self.rejection_detail is not None:
                raise ValueError("valid results cannot contain rejection fields")
            if self.breach_count != len(self.breaches):
                raise ValueError("breach_count must match breaches")
            expected_max = max(
                (breach.normalized_excess for breach in self.breaches),
                default=0.0,
            )
            expected_total = math.fsum(breach.normalized_excess for breach in self.breaches)
            if not math.isclose(self.maximum_normalized_excess, expected_max, abs_tol=1e-12):
                raise ValueError("maximum_normalized_excess does not match breaches")
            if not math.isclose(self.total_normalized_excess, expected_total, abs_tol=1e-12):
                raise ValueError("total_normalized_excess does not match breaches")
        else:
            if self.metrics is not None or self.breaches or self.historical_window is not None:
                raise ValueError("rejected results cannot contain metrics or breaches")
            if self.rejection_code is None or self.rejection_detail is None:
                raise ValueError("rejected results require typed rejection details")
            if self.rank is not None:
                raise ValueError("rejected results cannot have a rank")
            ranking_values = (
                self.breach_count,
                self.maximum_normalized_excess,
                self.total_normalized_excess,
                self.worst_portfolio_loss,
            )
            if any(ranking_values):
                raise ValueError("rejected results cannot contain ranking evidence")
        return self


class DefenderVerdict(ContractModel):
    """Independent provenance and metric-replay verdict."""

    schema_version: SchemaVersion = "1.0"
    scenario_id: ScenarioIdentifier
    verdict: DefenderVerdictValue
    schema_valid: StrictBool
    data_hash_matches: StrictBool
    config_hash_matches: StrictBool
    code_version_matches: StrictBool
    scenario_identity_matches: StrictBool
    budget_valid: StrictBool
    result_matches: StrictBool
    event_dates_match: StrictBool
    transform_hash_matches: StrictBool
    replay_metrics: MetricSet | None
    max_metric_delta: NonNegativeFloat | None
    comparison_tolerance: PositiveFloat
    reasons: tuple[NarrativeText, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_verdict_contract(self) -> Self:
        """Require verdict evidence consistent with provenance and replay."""
        checks = (
            self.schema_valid,
            self.data_hash_matches,
            self.config_hash_matches,
            self.code_version_matches,
            self.scenario_identity_matches,
            self.budget_valid,
        )
        all_checks_pass = all(checks)
        replay_matches = (
            self.result_matches
            and self.event_dates_match
            and self.transform_hash_matches
        )
        if self.verdict is DefenderVerdictValue.REPRODUCED:
            if not all_checks_pass or not replay_matches:
                raise ValueError("reproduced evidence requires every check and replay to pass")
            if self.replay_metrics is None or self.max_metric_delta is None:
                raise ValueError("reproduced evidence requires replay metrics and delta")
            if self.max_metric_delta > self.comparison_tolerance:
                raise ValueError("reproduced metric delta exceeds tolerance")
            if self.reasons:
                raise ValueError("reproduced evidence cannot contain rejection reasons")
        elif self.verdict is DefenderVerdictValue.NOT_REPRODUCED:
            if not all_checks_pass:
                raise ValueError("not_reproduced requires valid provenance")
            replay_was_rejected = (
                self.replay_metrics is None and self.max_metric_delta is None
            )
            replay_has_metrics = (
                self.replay_metrics is not None and self.max_metric_delta is not None
            )
            if not replay_was_rejected and not replay_has_metrics:
                raise ValueError("not_reproduced replay metrics and delta must appear together")
            numeric_mismatch = (
                self.max_metric_delta is not None
                and self.max_metric_delta > self.comparison_tolerance
            )
            if replay_matches and not numeric_mismatch:
                raise ValueError("not_reproduced requires a material replay mismatch")
            if not self.reasons:
                raise ValueError("not_reproduced requires a reason")
        else:
            if all_checks_pass:
                raise ValueError("invalid_evidence requires at least one failed check")
            if any(
                (
                    self.result_matches,
                    self.event_dates_match,
                    self.transform_hash_matches,
                )
            ):
                raise ValueError("invalid_evidence cannot claim a matching replay")
            if self.replay_metrics is not None or self.max_metric_delta is not None:
                raise ValueError("invalid_evidence cannot contain replay metrics")
            if not self.reasons:
                raise ValueError("invalid_evidence requires a reason")
        return self


class FailureReport(ContractModel):
    """Research-only report containing only independently reproduced results."""

    schema_version: SchemaVersion = "1.0"
    notice: Literal["Research only; not investment advice."]
    experiment_id: Identifier
    data_sha256: Sha256
    config_sha256: Sha256
    code_version: Identifier
    seed: Annotated[int, Field(strict=True, ge=0, le=4_294_967_295)]
    baseline_metrics: MetricSet | None
    verified_results: tuple[StressResult, ...] = Field(default=(), max_length=TOP_K)
    defender_verdicts: tuple[DefenderVerdict, ...] = Field(default=(), max_length=TOP_K)
    scenario_explanations: dict[ScenarioIdentifier, NarrativeText]
    limitations: tuple[NarrativeText, ...] = Field(min_length=1, max_length=16)
    summary: NarrativeText

    @model_validator(mode="after")
    def validate_report_contract(self) -> Self:
        """Allow only reproduced scenario evidence to support conclusions."""
        result_ids = [result.scenario_id for result in self.verified_results]
        if len(set(result_ids)) != len(result_ids):
            raise ValueError("verified result scenario IDs must be unique")
        if any(result.status is not ResultStatus.VALID for result in self.verified_results):
            raise ValueError("verified_results must contain valid engine evidence")
        if self.verified_results and self.baseline_metrics is None:
            raise ValueError("verified results require independently replayed baseline metrics")

        verdict_ids = [verdict.scenario_id for verdict in self.defender_verdicts]
        if len(set(verdict_ids)) != len(verdict_ids):
            raise ValueError("defender verdict scenario IDs must be unique")
        reproduced_ids = {
            verdict.scenario_id
            for verdict in self.defender_verdicts
            if verdict.verdict is DefenderVerdictValue.REPRODUCED
        }
        if set(result_ids) != reproduced_ids:
            raise ValueError("verified_results must exactly match reproduced verdicts")
        if set(self.scenario_explanations) != set(result_ids):
            raise ValueError("scenario_explanations must exactly match verified results")
        return self
