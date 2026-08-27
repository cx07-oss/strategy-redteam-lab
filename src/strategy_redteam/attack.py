"""Model-free bounded attack orchestration and deterministic evidence construction.

The proposer may supply only structured scenario candidates. Narrative fields remain
inert, policy validation never clamps numeric values, and all numerical market
evidence is produced by the deterministic stress and backtest engines.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, Self

import pandas as pd  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]
from pydantic import Field, ValidationError, model_validator

from strategy_redteam.backtest import (
    BacktestError,
    BacktestResult,
    run_backtest,
    run_backtest_with_asset_returns,
    validate_supplied_asset_returns,
)
from strategy_redteam.data import StoredDataset, canonical_manifest_bytes
from strategy_redteam.domain import (
    MAX_CANDIDATES_PER_ROUND,
    MAX_ROUNDS,
    MAX_STRESS_DURATION_ROWS,
    MAX_TOTAL_SCENARIOS,
    TOP_K,
    ComponentTransformSummary,
    ContractModel,
    DataManifest,
    ExperimentSpec,
    FiniteFloat,
    Identifier,
    MetricSet,
    NarrativeText,
    NonNegativeFloat,
    RejectionCode,
    ResultStatus,
    ReturnSummary,
    SchemaVersion,
    Sha256,
    StrategyKind,
    StrategySpec,
    StressComponent,
    StressFamily,
    StressResult,
    StressScenario,
    Symbol,
)
from strategy_redteam.strategy import FixedMonthly6040Strategy, Strategy
from strategy_redteam.stress import (
    STRESS_TRANSFORM_VERSION,
    StressArithmeticError,
    StressCorrelationError,
    StressTransformError,
    StressTransformResult,
    StressValidationError,
    StressWindowError,
    apply_stress_scenario,
)

if TYPE_CHECKING:
    np: Any
else:
    import numpy as np

ATTACK_RUNNER_VERSION = "attack-runner-1.0"
MAX_POLICY_BYTES = 65_536
_SCENARIO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

CandidatePayload = StressScenario | Mapping[str, object]
Clock = Callable[[], float]


class AttackError(Exception):
    """Base class for bounded attack-runner failures."""


class AttackValidationError(AttackError):
    """The attack context cannot satisfy its typed provenance contract."""


class AttackBudgetExceeded(AttackError):
    """A proposer or caller attempted to exceed a hard attack budget."""


class AttackPolicyError(AttackError):
    """A versioned policy file is invalid or unsafe to interpret."""


class AttackPolicyViolation(AttackError):
    """A typed scenario is outside the configured policy and is not clamped."""


class OfflineProposalError(AttackError):
    """The deterministic offline proposer cannot fit a candidate to the dataset."""


class StopReason(StrEnum):
    """Stable bounded-run termination reasons."""

    EVIDENCE_CONDITION_MET = "evidence_condition_met"
    TIMEOUT = "timeout"
    MAX_ROUNDS_REACHED = "max_rounds_reached"
    MAX_TOTAL_SCENARIOS_REACHED = "max_total_scenarios_reached"
    PROPOSER_EXHAUSTED = "proposer_exhausted"
    PROPOSER_BUDGET_VIOLATION = "proposer_budget_violation"


class ProposalDecision(StrEnum):
    """Whether a proposal crossed the schema, de-duplication, and policy boundary."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class NumericRange(ContractModel):
    """Inclusive finite policy bounds for one floating-point parameter."""

    schema_version: SchemaVersion = "1.0"
    minimum: FiniteFloat
    maximum: FiniteFloat

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("numeric range minimum must not exceed maximum")
        return self

    def contains(self, value: float) -> bool:
        """Return whether a value is inside the inclusive policy range."""
        return self.minimum <= value <= self.maximum


class IntegerPolicyRange(ContractModel):
    """Inclusive strict-integer policy bounds."""

    schema_version: SchemaVersion = "1.0"
    minimum: Annotated[int, Field(strict=True, ge=1)]
    maximum: Annotated[int, Field(strict=True, ge=1)]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("integer range minimum must not exceed maximum")
        return self

    def contains(self, value: int) -> bool:
        """Return whether an integer is inside the inclusive policy range."""
        return self.minimum <= value <= self.maximum


class AttackNumericRanges(ContractModel):
    """Named numeric bounds whose meanings are fixed by the stress schema."""

    schema_version: SchemaVersion = "1.0"
    one_day_gap_shock: NumericRange
    sustained_cumulative_shock: NumericRange
    sustained_duration_rows: IntegerPolicyRange
    volatility_multiplier: NumericRange
    target_correlation: NumericRange
    transaction_cost_multiplier: NumericRange

    @model_validator(mode="after")
    def validate_domain_intersections(self) -> Self:
        if self.one_day_gap_shock.minimum <= -1.0:
            raise ValueError("one-day shock policy must remain strictly above -1")
        if self.sustained_cumulative_shock.minimum <= -1.0:
            raise ValueError("sustained shock policy must remain strictly above -1")
        if self.sustained_duration_rows.maximum > MAX_STRESS_DURATION_ROWS:
            raise ValueError("duration policy exceeds MAX_STRESS_DURATION_ROWS")
        if self.volatility_multiplier.minimum <= 0.0:
            raise ValueError("volatility policy must remain strictly positive")
        if not (-1.0 < self.target_correlation.minimum <= self.target_correlation.maximum < 1.0):
            raise ValueError("correlation policy must remain strictly inside (-1, 1)")
        if self.transaction_cost_multiplier.minimum <= 0.0:
            raise ValueError("transaction-cost policy must remain strictly positive")
        return self


class AttackHypothesisFamily(StrEnum):
    """Approved Gate 9 hypothesis families, independent of narrative wording."""

    INFLATION_CORRELATION_BREAK = "inflation_correlation_break"
    REBALANCE_TIMING_GAP = "rebalance_timing_gap"
    VOLATILITY_REGIME_JUMP = "volatility_regime_jump"
    TRADING_FRICTION_BREAK = "trading_friction_break"


class RequiredStrategyMechanism(StrEnum):
    """Strategy mechanisms that a policy row must prove before proposal."""

    SPY_TLT_SLEEVES = "spy_tlt_sleeves"
    FIXED_MONTHLY_REBALANCE = "fixed_monthly_rebalance"
    VOLATILITY_SIZING = "volatility_sizing"
    REBALANCE_TURNOVER = "rebalance_turnover"


def _require_numeric_range(
    value: NumericRange,
    expected_minimum: float,
    expected_maximum: float,
    name: str,
) -> None:
    if value.minimum != expected_minimum or value.maximum != expected_maximum:
        raise ValueError(
            f"{name} must equal the approved inclusive range "
            f"[{expected_minimum}, {expected_maximum}]"
        )


def _require_integer_range(
    value: IntegerPolicyRange,
    expected_minimum: int,
    expected_maximum: int,
    name: str,
) -> None:
    if value.minimum != expected_minimum or value.maximum != expected_maximum:
        raise ValueError(
            f"{name} must equal the approved inclusive range "
            f"[{expected_minimum}, {expected_maximum}]"
        )


class InflationCorrelationHypothesisPolicy(ContractModel):
    """Structured search dimensions for the diversification-break hypothesis."""

    schema_version: SchemaVersion = "1.0"
    hypothesis_family: Literal[AttackHypothesisFamily.INFLATION_CORRELATION_BREAK] = (
        AttackHypothesisFamily.INFLATION_CORRELATION_BREAK
    )
    required_strategy_mechanism: Literal[RequiredStrategyMechanism.SPY_TLT_SLEEVES] = (
        RequiredStrategyMechanism.SPY_TLT_SLEEVES
    )
    component_template: tuple[StressFamily, ...] = Field(min_length=3, max_length=3)
    target_correlation: NumericRange
    correlation_volatility_duration_rows: IntegerPolicyRange
    volatility_multiplier: NumericRange
    spy_cumulative_shock: NumericRange
    tlt_cumulative_shock: NumericRange
    shock_duration_rows: IntegerPolicyRange
    minimum_breach_count: Literal[1] = 1

    @model_validator(mode="after")
    def validate_approved_row(self) -> Self:
        expected = (
            StressFamily.VOLATILITY_MULTIPLIER,
            StressFamily.CORRELATION_TARGET,
            StressFamily.SUSTAINED_CUMULATIVE_SHOCK,
        )
        if self.component_template != expected:
            raise ValueError("inflation correlation components must use the approved order")
        _require_numeric_range(self.target_correlation, 0.25, 0.90, "target_correlation")
        _require_integer_range(
            self.correlation_volatility_duration_rows,
            20,
            126,
            "correlation_volatility_duration_rows",
        )
        _require_numeric_range(
            self.volatility_multiplier,
            1.25,
            3.00,
            "volatility_multiplier",
        )
        _require_numeric_range(
            self.spy_cumulative_shock,
            -0.25,
            -0.05,
            "spy_cumulative_shock",
        )
        _require_numeric_range(
            self.tlt_cumulative_shock,
            -0.20,
            -0.04,
            "tlt_cumulative_shock",
        )
        _require_integer_range(self.shock_duration_rows, 5, 20, "shock_duration_rows")
        return self


class RebalanceTimingHypothesisPolicy(ContractModel):
    """Structured search dimensions for the pre-rebalance gap hypothesis."""

    schema_version: SchemaVersion = "1.0"
    hypothesis_family: Literal[AttackHypothesisFamily.REBALANCE_TIMING_GAP] = (
        AttackHypothesisFamily.REBALANCE_TIMING_GAP
    )
    required_strategy_mechanism: Literal[RequiredStrategyMechanism.FIXED_MONTHLY_REBALANCE] = (
        RequiredStrategyMechanism.FIXED_MONTHLY_REBALANCE
    )
    component_template: tuple[StressFamily, ...] = Field(min_length=1, max_length=1)
    rebalance_offsets_rows: tuple[Annotated[int, Field(strict=True)], ...] = Field(
        min_length=3, max_length=3
    )
    spy_one_day_gap: NumericRange
    tlt_one_day_gap: NumericRange
    minimum_stale_weight_underperformance: FiniteFloat = 0.0025

    @model_validator(mode="after")
    def validate_approved_row(self) -> Self:
        if self.component_template != (StressFamily.ONE_DAY_GAP,):
            raise ValueError("rebalance timing must use one one_day_gap component")
        if self.rebalance_offsets_rows != (-3, -2, -1):
            raise ValueError("rebalance offsets must equal the approved {-3, -2, -1} rows")
        _require_numeric_range(self.spy_one_day_gap, -0.15, -0.03, "spy_one_day_gap")
        _require_numeric_range(self.tlt_one_day_gap, -0.12, -0.02, "tlt_one_day_gap")
        if self.minimum_stale_weight_underperformance != 0.0025:
            raise ValueError("minimum stale-weight underperformance must equal 0.0025")
        return self


class VolatilityRegimeHypothesisPolicy(ContractModel):
    """Search dimensions retained only for strategies proving volatility sizing."""

    schema_version: SchemaVersion = "1.0"
    hypothesis_family: Literal[AttackHypothesisFamily.VOLATILITY_REGIME_JUMP] = (
        AttackHypothesisFamily.VOLATILITY_REGIME_JUMP
    )
    required_strategy_mechanism: Literal[RequiredStrategyMechanism.VOLATILITY_SIZING] = (
        RequiredStrategyMechanism.VOLATILITY_SIZING
    )
    component_template: tuple[StressFamily, ...] = Field(min_length=1, max_length=1)
    lookback_rows: IntegerPolicyRange
    volatility_multiplier: NumericRange
    stress_duration_rows: IntegerPolicyRange

    @model_validator(mode="after")
    def validate_approved_row(self) -> Self:
        if self.component_template != (StressFamily.VOLATILITY_MULTIPLIER,):
            raise ValueError("volatility regime jump must use a volatility component")
        _require_integer_range(self.lookback_rows, 20, 60, "lookback_rows")
        _require_numeric_range(
            self.volatility_multiplier,
            1.50,
            3.00,
            "volatility_multiplier",
        )
        _require_integer_range(self.stress_duration_rows, 5, 20, "stress_duration_rows")
        return self


class TradingFrictionHypothesisPolicy(ContractModel):
    """Structured search dimensions and materiality boundaries for friction."""

    schema_version: SchemaVersion = "1.0"
    hypothesis_family: Literal[AttackHypothesisFamily.TRADING_FRICTION_BREAK] = (
        AttackHypothesisFamily.TRADING_FRICTION_BREAK
    )
    required_strategy_mechanism: Literal[RequiredStrategyMechanism.REBALANCE_TURNOVER] = (
        RequiredStrategyMechanism.REBALANCE_TURNOVER
    )
    component_template: tuple[StressFamily, ...] = Field(min_length=1, max_length=1)
    transaction_cost_multiplier: NumericRange
    minimum_incremental_cost_contribution: FiniteFloat = 0.005
    minimum_cost_share_of_absolute_loss: FiniteFloat = 0.10

    @model_validator(mode="after")
    def validate_approved_row(self) -> Self:
        if self.component_template != (StressFamily.TRANSACTION_COST_MULTIPLIER,):
            raise ValueError("trading friction must use one transaction-cost component")
        _require_numeric_range(
            self.transaction_cost_multiplier,
            2.00,
            5.00,
            "transaction_cost_multiplier",
        )
        if self.minimum_incremental_cost_contribution != 0.005:
            raise ValueError("minimum incremental cost contribution must equal 0.005")
        if self.minimum_cost_share_of_absolute_loss != 0.10:
            raise ValueError("minimum cost share must equal 0.10")
        return self


AttackHypothesisPolicy = Annotated[
    InflationCorrelationHypothesisPolicy
    | RebalanceTimingHypothesisPolicy
    | VolatilityRegimeHypothesisPolicy
    | TradingFrictionHypothesisPolicy,
    Field(discriminator="hypothesis_family"),
]


@dataclass(frozen=True)
class AttackValidationContext:
    """Deterministic context for hypothesis assumptions that schemas cannot prove."""

    strategy_spec: StrategySpec
    market_dates: tuple[date, ...]
    rebalance_dates: tuple[date, ...]
    transaction_cost_bps: float
    positive_turnover_dates: frozenset[date]


class EvidenceCondition(ContractModel):
    """Configured condition that stops later proposal rounds once satisfied."""

    schema_version: SchemaVersion = "1.0"
    minimum_failure_scenarios: Annotated[int, Field(strict=True, ge=1, le=TOP_K)]
    minimum_breach_count: Annotated[int, Field(strict=True, ge=1, le=3)]
    minimum_maximum_normalized_excess: NonNegativeFloat

    def qualifies(self, result: StressResult) -> bool:
        """Use only typed engine summaries to decide whether evidence qualifies."""
        return (
            result.status is ResultStatus.VALID
            and result.breach_count >= self.minimum_breach_count
            and result.maximum_normalized_excess >= self.minimum_maximum_normalized_excess
        )


class AttackPolicy(ContractModel):
    """Versioned transform envelope plus approved structured hypothesis rows."""

    schema_version: Literal["1.0"] = "1.0"
    policy_id: Identifier
    allowed_families: tuple[StressFamily, ...] = Field(min_length=1, max_length=5)
    numeric_ranges: AttackNumericRanges
    hypotheses: tuple[AttackHypothesisPolicy, ...] = Field(default=(), max_length=4)
    evidence_condition: EvidenceCondition

    @model_validator(mode="after")
    def validate_supported_families(self) -> Self:
        if len(set(self.allowed_families)) != len(self.allowed_families):
            raise ValueError("allowed_families must be unique")
        if StressFamily.HISTORICAL_WINDOW in self.allowed_families:
            raise ValueError(
                "historical_window uses the Gate 4 scanner and is not a synthetic attack family"
            )
        hypothesis_families = [row.hypothesis_family for row in self.hypotheses]
        if len(set(hypothesis_families)) != len(hypothesis_families):
            raise ValueError("hypothesis families must be unique")
        allowed = set(self.allowed_families)
        for row in self.hypotheses:
            if not set(row.component_template).issubset(allowed):
                raise ValueError("hypothesis component template uses a disallowed family")
        self._validate_hypothesis_ranges_fit_machine_envelope()
        return self

    def _validate_hypothesis_ranges_fit_machine_envelope(self) -> None:
        ranges = self.numeric_ranges
        for row in self.hypotheses:
            if isinstance(row, InflationCorrelationHypothesisPolicy):
                checks = (
                    (ranges.target_correlation, row.target_correlation),
                    (ranges.volatility_multiplier, row.volatility_multiplier),
                    (ranges.sustained_cumulative_shock, row.spy_cumulative_shock),
                    (ranges.sustained_cumulative_shock, row.tlt_cumulative_shock),
                )
                if not all(
                    machine.contains(specific.minimum) and machine.contains(specific.maximum)
                    for machine, specific in checks
                ):
                    raise ValueError("inflation hypothesis broadens the machine numeric envelope")
                duration = ranges.sustained_duration_rows
                if not (
                    duration.contains(row.shock_duration_rows.minimum)
                    and duration.contains(row.shock_duration_rows.maximum)
                ):
                    raise ValueError("inflation duration broadens the machine numeric envelope")
            elif isinstance(row, RebalanceTimingHypothesisPolicy):
                if not all(
                    ranges.one_day_gap_shock.contains(value)
                    for specific in (row.spy_one_day_gap, row.tlt_one_day_gap)
                    for value in (specific.minimum, specific.maximum)
                ):
                    raise ValueError("rebalance hypothesis broadens the machine numeric envelope")
            elif isinstance(row, VolatilityRegimeHypothesisPolicy):
                if not all(
                    ranges.volatility_multiplier.contains(value)
                    for value in (
                        row.volatility_multiplier.minimum,
                        row.volatility_multiplier.maximum,
                    )
                ):
                    raise ValueError("volatility hypothesis broadens the machine numeric envelope")
            elif not all(
                ranges.transaction_cost_multiplier.contains(value)
                for value in (
                    row.transaction_cost_multiplier.minimum,
                    row.transaction_cost_multiplier.maximum,
                )
            ):
                raise ValueError("friction hypothesis broadens the machine numeric envelope")

    def for_strategy(self, strategy_spec: StrategySpec) -> AttackPolicy:
        """Remove policy rows whose required mechanism is absent from StrategySpec."""
        if not self.hypotheses:
            return self
        active = tuple(row for row in self.hypotheses if _strategy_supports(row, strategy_spec))
        return self.model_copy(update={"hypotheses": active})

    def hypothesis_for_scenario(
        self,
        scenario: StressScenario,
    ) -> AttackHypothesisPolicy | None:
        """Classify only from ordered numeric components; narrative is ignored."""
        component_families = tuple(component.family for component in scenario.components)
        matches = tuple(
            row for row in self.hypotheses if row.component_template == component_families
        )
        if len(matches) > 1:
            raise AttackPolicyViolation("numeric component template matches multiple hypotheses")
        return matches[0] if matches else None

    def validate_scenario(
        self,
        scenario: StressScenario,
        *,
        context: AttackValidationContext | None = None,
    ) -> None:
        """Reject an out-of-policy scenario without changing any supplied value."""
        allowed = set(self.allowed_families)
        ranges = self.numeric_ranges
        for component in scenario.components:
            if component.family not in allowed:
                raise AttackPolicyViolation(
                    f"family {component.family.value} is not allowed by policy {self.policy_id}"
                )
            if component.family is StressFamily.ONE_DAY_GAP:
                self._validate_shocks(component, ranges.one_day_gap_shock)
            elif component.family is StressFamily.SUSTAINED_CUMULATIVE_SHOCK:
                self._validate_shocks(component, ranges.sustained_cumulative_shock)
                if component.duration_rows is None or not ranges.sustained_duration_rows.contains(
                    component.duration_rows
                ):
                    raise AttackPolicyViolation("duration_rows is outside the policy range")
            elif component.family is StressFamily.VOLATILITY_MULTIPLIER:
                value = component.volatility_multiplier
                if value is None or not ranges.volatility_multiplier.contains(value):
                    raise AttackPolicyViolation("volatility_multiplier is outside the policy range")
            elif component.family is StressFamily.CORRELATION_TARGET:
                value = component.target_correlation
                if value is None or not ranges.target_correlation.contains(value):
                    raise AttackPolicyViolation("target_correlation is outside the policy range")
            elif component.family is StressFamily.TRANSACTION_COST_MULTIPLIER:
                value = component.transaction_cost_multiplier
                if value is None or not ranges.transaction_cost_multiplier.contains(value):
                    raise AttackPolicyViolation(
                        "transaction_cost_multiplier is outside the policy range"
                    )

        if not self.hypotheses:
            return
        if scenario.headline is None:
            raise AttackPolicyViolation("approved hypothesis candidates require an inert headline")
        row = self.hypothesis_for_scenario(scenario)
        if row is None:
            raise AttackPolicyViolation(
                "ordered numeric components do not match an active hypothesis template"
            )
        self._validate_hypothesis_structure(scenario, row)
        if context is not None:
            if not _strategy_supports(row, context.strategy_spec):
                raise AttackPolicyViolation(
                    f"hypothesis {row.hypothesis_family.value} is not applicable to StrategySpec"
                )
            self._validate_hypothesis_context(scenario, row, context)

    @staticmethod
    def _validate_hypothesis_structure(
        scenario: StressScenario,
        row: AttackHypothesisPolicy,
    ) -> None:
        if isinstance(row, InflationCorrelationHypothesisPolicy):
            volatility, correlation, shock = scenario.components
            if (
                volatility.start_date != correlation.start_date
                or volatility.end_date != correlation.end_date
            ):
                raise AttackPolicyViolation(
                    "inflation volatility and correlation windows must be identical"
                )
            if volatility.symbols != (Symbol.SPY, Symbol.TLT):
                raise AttackPolicyViolation("inflation volatility must include exactly SPY and TLT")
            if volatility.volatility_multiplier is None or not row.volatility_multiplier.contains(
                volatility.volatility_multiplier
            ):
                raise AttackPolicyViolation(
                    "inflation volatility_multiplier is outside its hypothesis range"
                )
            if correlation.target_correlation is None or not row.target_correlation.contains(
                correlation.target_correlation
            ):
                raise AttackPolicyViolation(
                    "inflation target_correlation is outside its hypothesis range"
                )
            if shock.duration_rows is None or not row.shock_duration_rows.contains(
                shock.duration_rows
            ):
                raise AttackPolicyViolation("inflation shock duration is outside its range")
            if shock.shocks is None or set(shock.shocks) != {Symbol.SPY, Symbol.TLT}:
                raise AttackPolicyViolation("inflation shock must contain exactly SPY and TLT")
            if not row.spy_cumulative_shock.contains(shock.shocks[Symbol.SPY]):
                raise AttackPolicyViolation("inflation SPY shock is outside its hypothesis range")
            if not row.tlt_cumulative_shock.contains(shock.shocks[Symbol.TLT]):
                raise AttackPolicyViolation("inflation TLT shock is outside its hypothesis range")
        elif isinstance(row, RebalanceTimingHypothesisPolicy):
            gap = scenario.components[0]
            if gap.shocks is None:
                raise AttackPolicyViolation("rebalance gap must shock at least one sleeve")
            if Symbol.SPY in gap.shocks and not row.spy_one_day_gap.contains(
                gap.shocks[Symbol.SPY]
            ):
                raise AttackPolicyViolation("rebalance SPY gap is outside its hypothesis range")
            if Symbol.TLT in gap.shocks and not row.tlt_one_day_gap.contains(
                gap.shocks[Symbol.TLT]
            ):
                raise AttackPolicyViolation("rebalance TLT gap is outside its hypothesis range")
        elif isinstance(row, VolatilityRegimeHypothesisPolicy):
            volatility = scenario.components[0]
            if volatility.volatility_multiplier is None or not row.volatility_multiplier.contains(
                volatility.volatility_multiplier
            ):
                raise AttackPolicyViolation(
                    "volatility-regime multiplier is outside its hypothesis range"
                )
            if volatility.symbols != (Symbol.SPY, Symbol.TLT):
                raise AttackPolicyViolation(
                    "volatility-regime stress must include exactly SPY and TLT"
                )
        else:
            multiplier = scenario.components[0].transaction_cost_multiplier
            if multiplier is None or not row.transaction_cost_multiplier.contains(multiplier):
                raise AttackPolicyViolation("friction multiplier is outside its hypothesis range")

    @staticmethod
    def _validate_hypothesis_context(
        scenario: StressScenario,
        row: AttackHypothesisPolicy,
        context: AttackValidationContext,
    ) -> None:
        positions = {market_date: index for index, market_date in enumerate(context.market_dates)}
        if scenario.evaluation_start not in positions or scenario.evaluation_end not in positions:
            raise AttackPolicyViolation("evaluation endpoints must be observed market dates")
        if isinstance(row, InflationCorrelationHypothesisPolicy):
            volatility = scenario.components[0]
            shock = scenario.components[2]
            if volatility.start_date not in positions or volatility.end_date not in positions:
                raise AttackPolicyViolation("inflation window endpoints must be observed dates")
            start_position = positions[volatility.start_date]
            end_position = positions[volatility.end_date]
            window_rows = end_position - start_position + 1
            if not row.correlation_volatility_duration_rows.contains(window_rows):
                raise AttackPolicyViolation(
                    "inflation correlation/volatility window is outside 20..126 rows"
                )
            if shock.start_date not in positions or shock.duration_rows is None:
                raise AttackPolicyViolation("inflation shock start must be an observed date")
            shock_end = positions[shock.start_date] + shock.duration_rows - 1
            if shock_end >= len(context.market_dates):
                raise AttackPolicyViolation("inflation shock exceeds the observed market dates")
            if context.market_dates[shock_end] > scenario.evaluation_end:
                raise AttackPolicyViolation("inflation shock exceeds the evaluation window")
        elif isinstance(row, RebalanceTimingHypothesisPolicy):
            gap_date = scenario.components[0].date
            if gap_date not in positions:
                raise AttackPolicyViolation("rebalance gap date must be observed")
            gap_position = positions[gap_date]
            valid_gap_positions = {
                positions[rebalance_date] + offset
                for rebalance_date in context.rebalance_dates
                if rebalance_date in positions
                for offset in row.rebalance_offsets_rows
                if 0 <= positions[rebalance_date] + offset < positions[rebalance_date]
            }
            if gap_position not in valid_gap_positions:
                raise AttackPolicyViolation(
                    "rebalance gap must resolve to -3, -2, or -1 rows before a rebalance"
                )
        elif isinstance(row, VolatilityRegimeHypothesisPolicy):
            volatility = scenario.components[0]
            if volatility.start_date not in positions or volatility.end_date not in positions:
                raise AttackPolicyViolation("volatility-regime window endpoints must be observed")
            duration = positions[volatility.end_date] - positions[volatility.start_date] + 1
            if not row.stress_duration_rows.contains(duration):
                raise AttackPolicyViolation("volatility-regime duration is outside 5..20 rows")
        else:
            if context.transaction_cost_bps <= 0.0:
                raise AttackPolicyViolation(
                    "trading-friction hypothesis requires positive baseline transaction cost"
                )
            multiplier = scenario.components[0].transaction_cost_multiplier
            if multiplier is None or context.transaction_cost_bps * multiplier >= 10_000.0:
                raise AttackPolicyViolation(
                    "resulting transaction cost must remain inside [0, 10000) bps"
                )
            has_turnover = any(
                scenario.evaluation_start <= trade_date <= scenario.evaluation_end
                for trade_date in context.positive_turnover_dates
            )
            if not has_turnover:
                raise AttackPolicyViolation(
                    "trading-friction hypothesis requires a trade in the evaluation window"
                )

    @staticmethod
    def _validate_shocks(component: StressComponent, allowed: NumericRange) -> None:
        if component.shocks is None:
            raise AttackPolicyViolation("shock component has no shocks")
        if any(not allowed.contains(value) for value in component.shocks.values()):
            raise AttackPolicyViolation("at least one shock is outside the policy range")


def _strategy_supports(
    row: AttackHypothesisPolicy,
    strategy_spec: StrategySpec,
) -> bool:
    mechanism = row.required_strategy_mechanism
    if mechanism is RequiredStrategyMechanism.SPY_TLT_SLEEVES:
        return strategy_spec.symbols == (Symbol.SPY, Symbol.TLT)
    if mechanism is RequiredStrategyMechanism.FIXED_MONTHLY_REBALANCE:
        return strategy_spec.kind is StrategyKind.MONTHLY_60_40
    if mechanism is RequiredStrategyMechanism.VOLATILITY_SIZING:
        # No current StrategySpec kind declares a lookback, target, threshold, or sizing trigger.
        return False
    return strategy_spec.kind in {StrategyKind.MONTHLY_60_40, StrategyKind.EXTERNAL_WEIGHTS}


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Safe YAML loader that rejects duplicate keys instead of overwriting them."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise AttackPolicyError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_attack_policy(path: Path) -> AttackPolicy:
    """Load a bounded versioned policy with safe YAML semantics."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise AttackPolicyError(f"cannot read attack policy: {error}") from error
    if not content or len(content) > MAX_POLICY_BYTES:
        raise AttackPolicyError(f"attack policy must contain 1..{MAX_POLICY_BYTES} bytes")
    try:
        text = content.decode("utf-8")
        payload = yaml.load(text, Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise AttackPolicyError(f"invalid UTF-8 YAML policy: {error}") from error
    if not isinstance(payload, dict):
        raise AttackPolicyError("attack policy root must be a mapping")
    try:
        return AttackPolicy.model_validate(payload)
    except ValidationError as error:
        raise AttackPolicyError(f"attack policy validation failed: {error}") from error


class CandidateProposer(Protocol):
    """Bounded structured proposer interface; implementations cannot calculate metrics."""

    def propose(
        self,
        *,
        round_number: int,
        max_candidates: int,
        prior_results: tuple[StressResult, ...],
    ) -> Sequence[CandidatePayload]:
        """Return at most ``max_candidates`` JSON-like or typed candidates."""


@dataclass(frozen=True)
class DeterministicOfflineProposer:
    """Seeded, model-free candidate grid for tests and local development."""

    market_dates: tuple[date, ...]
    policy: AttackPolicy
    seed: int

    @classmethod
    def from_dataset(
        cls,
        dataset: StoredDataset,
        policy: AttackPolicy,
        seed: int,
    ) -> DeterministicOfflineProposer:
        """Build a proposer from immutable observed market dates."""
        dates = tuple(timestamp.date() for timestamp in dataset.data.index)
        if len(dates) < 3:
            raise OfflineProposalError("offline proposal requires at least three market dates")
        return cls(market_dates=dates, policy=policy, seed=seed)

    def propose(
        self,
        *,
        round_number: int,
        max_candidates: int,
        prior_results: tuple[StressResult, ...],
    ) -> Sequence[CandidatePayload]:
        """Return a deterministic batch; prior evidence is accepted but never invented."""
        del prior_results
        if not 1 <= round_number <= MAX_ROUNDS:
            raise OfflineProposalError("round_number is outside the hard budget")
        if not 0 <= max_candidates <= MAX_CANDIDATES_PER_ROUND:
            raise OfflineProposalError("max_candidates is outside the hard budget")
        return build_deterministic_candidates(
            market_dates=self.market_dates,
            policy=self.policy,
            seed=self.seed,
            round_number=round_number,
            max_candidates=max_candidates,
        )

    def _scenario(self, round_number: int, candidate_number: int) -> StressScenario:
        ordinal = (round_number - 1) * MAX_CANDIDATES_PER_ROUND + candidate_number
        fraction = ordinal / (MAX_TOTAL_SCENARIOS + 1.0)
        if self.policy.hypotheses:
            row_index = (ordinal - 1 + self.seed) % len(self.policy.hypotheses)
            row = self.policy.hypotheses[row_index]
            return StressScenario(
                scenario_id=f"offline-r{round_number:02d}-c{candidate_number:02d}",
                evaluation_start=self.market_dates[0],
                evaluation_end=self.market_dates[-1],
                components=self._hypothesis_components(row, ordinal, fraction),
                hypothesis=(
                    f"Bounded {row.hypothesis_family.value} candidate; only the ordered "
                    "typed components have executable meaning."
                ),
                headline=f"Bounded offline {row.hypothesis_family.value} candidate",
            )
        families = self.policy.allowed_families
        family_index = (ordinal - 1 + self.seed) % len(families)
        family = families[family_index]
        component = self._component(family, ordinal, fraction)
        return StressScenario(
            scenario_id=f"offline-r{round_number:02d}-c{candidate_number:02d}",
            evaluation_start=self.market_dates[0],
            evaluation_end=self.market_dates[-1],
            components=(component,),
            hypothesis=(
                "Deterministic offline candidate; only the typed numeric component "
                "has executable meaning."
            ),
            headline=None,
        )

    def _hypothesis_components(
        self,
        row: AttackHypothesisPolicy,
        ordinal: int,
        fraction: float,
    ) -> tuple[StressComponent, ...]:
        if isinstance(row, InflationCorrelationHypothesisPolicy):
            available = len(self.market_dates)
            minimum_required = (
                row.correlation_volatility_duration_rows.minimum + row.shock_duration_rows.minimum
            )
            if available < minimum_required:
                raise OfflineProposalError("dataset is too short for the inflation hypothesis")
            maximum_window = min(
                row.correlation_volatility_duration_rows.maximum,
                available - row.shock_duration_rows.minimum,
            )
            window_span = maximum_window - row.correlation_volatility_duration_rows.minimum
            window_rows = row.correlation_volatility_duration_rows.minimum + round(
                window_span * fraction
            )
            maximum_shock_duration = min(
                row.shock_duration_rows.maximum,
                available - window_rows,
            )
            shock_span = maximum_shock_duration - row.shock_duration_rows.minimum
            shock_duration = row.shock_duration_rows.minimum + round(shock_span * fraction)
            start = self.market_dates[0]
            end = self.market_dates[window_rows - 1]
            return (
                StressComponent(
                    family=StressFamily.VOLATILITY_MULTIPLIER,
                    start_date=start,
                    end_date=end,
                    symbols=(Symbol.SPY, Symbol.TLT),
                    volatility_multiplier=_interpolate(
                        row.volatility_multiplier,
                        fraction,
                    ),
                ),
                StressComponent(
                    family=StressFamily.CORRELATION_TARGET,
                    start_date=start,
                    end_date=end,
                    target_correlation=_interpolate(row.target_correlation, fraction),
                ),
                StressComponent(
                    family=StressFamily.SUSTAINED_CUMULATIVE_SHOCK,
                    start_date=self.market_dates[window_rows],
                    duration_rows=shock_duration,
                    shocks={
                        Symbol.SPY: _interpolate(row.spy_cumulative_shock, fraction),
                        Symbol.TLT: _interpolate(row.tlt_cumulative_shock, fraction),
                    },
                ),
            )
        if isinstance(row, RebalanceTimingHypothesisPolicy):
            rebalance_positions = tuple(
                index
                for index in range(1, len(self.market_dates))
                if self.market_dates[index].month != self.market_dates[index - 1].month
                and index >= 3
            )
            if not rebalance_positions:
                raise OfflineProposalError("dataset has no rebalance with three preceding rows")
            rebalance_position = rebalance_positions[
                (ordinal + self.seed) % len(rebalance_positions)
            ]
            offset = row.rebalance_offsets_rows[
                (ordinal + self.seed) % len(row.rebalance_offsets_rows)
            ]
            return (
                StressComponent(
                    family=StressFamily.ONE_DAY_GAP,
                    date=self.market_dates[rebalance_position + offset],
                    shocks={
                        Symbol.SPY: _interpolate(row.spy_one_day_gap, fraction),
                        Symbol.TLT: _interpolate(row.tlt_one_day_gap, fraction),
                    },
                ),
            )
        if isinstance(row, VolatilityRegimeHypothesisPolicy):
            maximum_duration = min(row.stress_duration_rows.maximum, len(self.market_dates))
            if maximum_duration < row.stress_duration_rows.minimum:
                raise OfflineProposalError("dataset is too short for volatility regime stress")
            duration = row.stress_duration_rows.minimum + round(
                (maximum_duration - row.stress_duration_rows.minimum) * fraction
            )
            return (
                StressComponent(
                    family=StressFamily.VOLATILITY_MULTIPLIER,
                    start_date=self.market_dates[0],
                    end_date=self.market_dates[duration - 1],
                    symbols=(Symbol.SPY, Symbol.TLT),
                    volatility_multiplier=_interpolate(
                        row.volatility_multiplier,
                        fraction,
                    ),
                ),
            )
        return (
            StressComponent(
                family=StressFamily.TRANSACTION_COST_MULTIPLIER,
                transaction_cost_multiplier=_interpolate(
                    row.transaction_cost_multiplier,
                    fraction,
                ),
            ),
        )

    def _component(
        self,
        family: StressFamily,
        ordinal: int,
        fraction: float,
    ) -> StressComponent:
        ranges = self.policy.numeric_ranges
        if family is StressFamily.ONE_DAY_GAP:
            value = _interpolate(ranges.one_day_gap_shock, fraction)
            position = 1 + (ordinal + self.seed) % (len(self.market_dates) - 1)
            return StressComponent(
                family=family,
                date=self.market_dates[position],
                shocks={Symbol.SPY: value, Symbol.TLT: value},
            )
        if family is StressFamily.SUSTAINED_CUMULATIVE_SHOCK:
            value = _interpolate(ranges.sustained_cumulative_shock, fraction)
            duration_range = ranges.sustained_duration_rows
            maximum_duration = min(duration_range.maximum, len(self.market_dates) - 1)
            if maximum_duration < duration_range.minimum:
                raise OfflineProposalError("dataset is too short for the duration policy")
            duration_count = maximum_duration - duration_range.minimum + 1
            duration = duration_range.minimum + (ordinal + self.seed) % duration_count
            maximum_start = len(self.market_dates) - duration
            start_position = (ordinal + self.seed) % (maximum_start + 1)
            return StressComponent(
                family=family,
                start_date=self.market_dates[start_position],
                duration_rows=duration,
                shocks={Symbol.SPY: value, Symbol.TLT: value},
            )
        if family is StressFamily.VOLATILITY_MULTIPLIER:
            return StressComponent(
                family=family,
                start_date=self.market_dates[0],
                end_date=self.market_dates[-1],
                symbols=(Symbol.SPY, Symbol.TLT),
                volatility_multiplier=_interpolate(
                    ranges.volatility_multiplier,
                    fraction,
                ),
            )
        if family is StressFamily.CORRELATION_TARGET:
            return StressComponent(
                family=family,
                start_date=self.market_dates[0],
                end_date=self.market_dates[-1],
                target_correlation=_interpolate(ranges.target_correlation, fraction),
            )
        if family is StressFamily.TRANSACTION_COST_MULTIPLIER:
            return StressComponent(
                family=family,
                transaction_cost_multiplier=_interpolate(
                    ranges.transaction_cost_multiplier,
                    fraction,
                ),
            )
        raise OfflineProposalError(f"unsupported offline family: {family.value}")


def build_deterministic_candidates(
    *,
    market_dates: tuple[date, ...],
    policy: AttackPolicy,
    seed: int,
    round_number: int,
    max_candidates: int,
) -> tuple[StressScenario, ...]:
    """Return the existing bounded offline scenarios for reuse by future catalog builders."""
    source = DeterministicOfflineProposer(market_dates=market_dates, policy=policy, seed=seed)
    return tuple(
        source._scenario(round_number, candidate_number)
        for candidate_number in range(1, max_candidates + 1)
    )


@dataclass(frozen=True)
class AttackCatalogEntry:
    """One immutable, canonical candidate with a deterministic opaque catalog key."""

    attack_key: str
    scenario: StressScenario


@dataclass(frozen=True)
class AttackCatalog:
    """Ordered immutable catalog for a later provider-selection boundary."""

    entries: tuple[AttackCatalogEntry, ...]


def build_attack_catalog(candidates: Sequence[StressScenario]) -> AttackCatalog:
    """Assign stable keys without mutating or generating canonical scenarios."""
    return AttackCatalog(
        entries=tuple(
            AttackCatalogEntry(attack_key=f"atk_{index:03d}", scenario=scenario)
            for index, scenario in enumerate(candidates, start=1)
        )
    )


def _interpolate(value_range: NumericRange, fraction: float) -> float:
    return float(value_range.minimum + fraction * (value_range.maximum - value_range.minimum))


class AttackBudget:
    """Runtime accounting for every hard limit and the monotonic deadline."""

    def __init__(
        self,
        *,
        max_rounds: int,
        max_candidates_per_round: int,
        max_total_scenarios: int,
        top_k: int,
        timeout_seconds: float,
        clock: Clock = time.monotonic,
    ) -> None:
        integer_limits = (
            ("max_rounds", max_rounds, MAX_ROUNDS),
            (
                "max_candidates_per_round",
                max_candidates_per_round,
                MAX_CANDIDATES_PER_ROUND,
            ),
            ("max_total_scenarios", max_total_scenarios, MAX_TOTAL_SCENARIOS),
            ("top_k", top_k, TOP_K),
        )
        for name, value, hard_limit in integer_limits:
            if isinstance(value, bool) or not isinstance(value, int):
                raise AttackBudgetExceeded(f"{name} must be a strict integer")
            if not 1 <= value <= hard_limit:
                raise AttackBudgetExceeded(f"{name} exceeds its hard limit {hard_limit}")
        if max_total_scenarios > max_rounds * max_candidates_per_round:
            raise AttackBudgetExceeded("total scenario budget exceeds round capacity")
        if top_k > max_total_scenarios:
            raise AttackBudgetExceeded("top_k exceeds total scenario budget")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0.0
        ):
            raise AttackBudgetExceeded("timeout_seconds must be finite and positive")
        self.max_rounds = max_rounds
        self.max_candidates_per_round = max_candidates_per_round
        self.max_total_scenarios = max_total_scenarios
        self.top_k = top_k
        self.timeout_seconds = float(timeout_seconds)
        self._clock = clock
        self._deadline = clock() + self.timeout_seconds
        self.rounds_started = 0
        self.candidate_slots_consumed = 0

    @classmethod
    def from_experiment(
        cls,
        experiment: ExperimentSpec,
        clock: Clock = time.monotonic,
    ) -> AttackBudget:
        """Create runtime accounting from an already bounded experiment contract."""
        return cls(
            max_rounds=experiment.max_rounds,
            max_candidates_per_round=experiment.max_candidates_per_round,
            max_total_scenarios=experiment.max_total_scenarios,
            top_k=experiment.top_k,
            timeout_seconds=experiment.timeout_seconds,
            clock=clock,
        )

    @property
    def remaining_scenarios(self) -> int:
        return self.max_total_scenarios - self.candidate_slots_consumed

    def deadline_reached(self) -> bool:
        """Use a monotonic clock so wall-clock changes cannot extend execution."""
        return self._clock() >= self._deadline

    def start_round(self) -> None:
        if self.rounds_started >= self.max_rounds:
            raise AttackBudgetExceeded("round budget exhausted")
        self.rounds_started += 1

    def reserve_batch(self, candidate_count: int) -> None:
        """Consume slots before validation so invalid proposals count."""
        if isinstance(candidate_count, bool) or not isinstance(candidate_count, int):
            raise AttackBudgetExceeded("candidate count must be a strict integer")
        if not 0 <= candidate_count <= self.max_candidates_per_round:
            raise AttackBudgetExceeded("candidate batch exceeds the per-round budget")
        if candidate_count > self.remaining_scenarios:
            raise AttackBudgetExceeded("candidate batch exceeds the total scenario budget")
        self.candidate_slots_consumed += candidate_count


class ProposalRecord(ContractModel):
    """Sanitized validation record for one consumed proposal slot."""

    schema_version: SchemaVersion = "1.0"
    round_number: Annotated[int, Field(strict=True, ge=1, le=MAX_ROUNDS)]
    candidate_number: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_CANDIDATES_PER_ROUND),
    ]
    scenario_id: str
    input_sha256: Sha256
    semantic_sha256: Sha256 | None
    decision: ProposalDecision
    scenario: StressScenario | None
    rejection_code: RejectionCode | None = None
    rejection_detail: NarrativeText | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.decision is ProposalDecision.ACCEPTED:
            if self.scenario is None or self.semantic_sha256 is None:
                raise ValueError("accepted proposals require a typed semantic payload")
            if self.rejection_code is not None or self.rejection_detail is not None:
                raise ValueError("accepted proposals cannot contain rejection details")
        elif self.rejection_code is None or self.rejection_detail is None:
            raise ValueError("rejected proposals require typed rejection details")
        return self


class WorstWindowEvidence(ContractModel):
    """Exact linked return contributions for one engine-identified worst window."""

    schema_version: SchemaVersion = "1.0"
    rule_id: Identifier
    start_date: date
    end_date: date
    portfolio_compounded_return: FiniteFloat
    asset_return_contributions: dict[Symbol, FiniteFloat]
    transaction_cost_return_contribution: FiniteFloat
    average_effective_weights: dict[Symbol, FiniteFloat]

    @model_validator(mode="after")
    def validate_reconciliation(self) -> Self:
        expected = {Symbol.SPY, Symbol.TLT}
        if set(self.asset_return_contributions) != expected:
            raise ValueError("asset contributions must contain exactly SPY and TLT")
        if set(self.average_effective_weights) != expected:
            raise ValueError("average weights must contain exactly SPY and TLT")
        reconciled = math.fsum(self.asset_return_contributions.values())
        reconciled += self.transaction_cost_return_contribution
        if not math.isclose(reconciled, self.portfolio_compounded_return, abs_tol=1e-10):
            raise ValueError("linked contributions must reconcile to the window return")
        return self


class PerformanceChartPoint(ContractModel):
    """One engine-produced daily portfolio point for a validated stress scenario."""

    schema_version: SchemaVersion = "1.0"
    date: date
    baseline_equity: FiniteFloat
    stressed_equity: FiniteFloat
    stressed_drawdown: NonNegativeFloat


class ScenarioEvaluationRecord(ContractModel):
    """One typed rejection or complete deterministic scenario evidence record."""

    schema_version: SchemaVersion = "1.0"
    round_number: Annotated[int, Field(strict=True, ge=1, le=MAX_ROUNDS)]
    candidate_number: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_CANDIDATES_PER_ROUND),
    ]
    scenario: StressScenario | None
    result: StressResult
    transform_sha256: Sha256 | None = None
    component_summaries: tuple[ComponentTransformSummary, ...] = ()
    pre_transform_summary: ReturnSummary | None = None
    post_transform_summary: ReturnSummary | None = None
    worst_windows: tuple[WorstWindowEvidence, ...] = Field(default=(), max_length=3)
    chart_points: tuple[PerformanceChartPoint, ...] = Field(default=(), max_length=10_000)

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> Self:
        if self.result.status is ResultStatus.VALID:
            if (
                self.scenario is None
                or self.transform_sha256 is None
                or self.pre_transform_summary is None
                or self.post_transform_summary is None
            ):
                raise ValueError("valid evaluation requires transform evidence")
            if len(self.worst_windows) != self.result.breach_count:
                raise ValueError("worst-window evidence must match result breaches")
        elif any(
            (
                self.transform_sha256 is not None,
                bool(self.component_summaries),
                self.pre_transform_summary is not None,
                self.post_transform_summary is not None,
                bool(self.worst_windows),
                bool(self.chart_points),
            )
        ):
            raise ValueError("rejected evaluation cannot contain numerical evidence")
        return self


@dataclass(frozen=True)
class AttackRun:
    """Complete in-memory outcome ready for one atomic artifact publication."""

    experiment: ExperimentSpec
    dataset_manifest: DataManifest
    dataset_manifest_sha256: str
    policy: AttackPolicy
    config_sha256: str
    policy_sha256: str
    baseline_metrics: MetricSet
    proposals: tuple[ProposalRecord, ...]
    evaluations: tuple[ScenarioEvaluationRecord, ...]
    top_failures: tuple[ScenarioEvaluationRecord, ...]
    rounds_started: int
    candidate_slots_consumed: int
    evaluated_scenarios: int
    rejected_scenarios: int
    stop_reason: StopReason
    evidence_condition_met: bool
    attack_completed: bool


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a JSON-like value deterministically for evidence hashes."""
    if isinstance(value, ContractModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(
            value,
            default=_json_default,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _semantic_sha256(scenario: StressScenario) -> str:
    payload = {
        "components": [component.model_dump(mode="json") for component in scenario.components],
        "evaluation_end": scenario.evaluation_end.isoformat(),
        "evaluation_start": scenario.evaluation_start.isoformat(),
    }
    return canonical_json_sha256(payload)


def _safe_rejection_detail(detail: str) -> str:
    compact = " ".join(detail.split())
    return (compact or "Candidate rejected.")[:4000]


def _candidate_identity(
    raw: CandidatePayload,
    round_number: int,
    candidate_number: int,
) -> tuple[str, str]:
    try:
        payload: object = (
            raw.model_dump(mode="json") if isinstance(raw, StressScenario) else dict(raw)
        )
        input_sha256 = canonical_json_sha256(payload)
    except (TypeError, ValueError):
        payload = {
            "candidate_number": candidate_number,
            "invalid_payload_type": type(raw).__name__,
            "round_number": round_number,
        }
        input_sha256 = canonical_json_sha256(payload)
    candidate_id: object = None
    if isinstance(raw, StressScenario):
        candidate_id = raw.scenario_id
    elif isinstance(raw, Mapping):
        candidate_id = raw.get("scenario_id")
    if isinstance(candidate_id, str) and _SCENARIO_ID_PATTERN.fullmatch(candidate_id):
        return candidate_id, input_sha256
    return f"rejected-r{round_number:02d}-c{candidate_number:02d}", input_sha256


def _rejected_result(
    *,
    experiment: ExperimentSpec,
    dataset: StoredDataset,
    strategy: Strategy,
    scenario_id: str,
    input_sha256: str,
    config_sha256: str,
    rejection_code: RejectionCode,
    rejection_detail: str,
) -> StressResult:
    return StressResult(
        experiment_id=experiment.experiment_id,
        scenario_id=scenario_id,
        dataset_id=dataset.manifest.dataset_id,
        strategy_id=strategy.spec.strategy_id,
        input_sha256=input_sha256,
        config_sha256=config_sha256,
        data_sha256=dataset.manifest.sha256,
        code_version=experiment.code_version,
        engine_version=ATTACK_RUNNER_VERSION,
        status=ResultStatus.REJECTED,
        rejection_code=rejection_code,
        rejection_detail=_safe_rejection_detail(rejection_detail),
    )


def _prepare_candidate(
    raw: CandidatePayload,
    *,
    round_number: int,
    candidate_number: int,
    policy: AttackPolicy,
    validation_context: AttackValidationContext,
    seen_ids: set[str],
    seen_semantics: set[str],
) -> tuple[ProposalRecord, StressScenario | None]:
    scenario_id, input_sha256 = _candidate_identity(raw, round_number, candidate_number)
    provider_failure = raw.get("provider_failure") if isinstance(raw, Mapping) else None
    if isinstance(provider_failure, str):
        detail = _safe_rejection_detail(provider_failure)
        return (
            ProposalRecord(
                round_number=round_number,
                candidate_number=candidate_number,
                scenario_id=scenario_id,
                input_sha256=input_sha256,
                semantic_sha256=None,
                decision=ProposalDecision.REJECTED,
                scenario=None,
                rejection_code=RejectionCode.INVALID_PARAMETER,
                rejection_detail=detail,
            ),
            None,
        )
    try:
        scenario = raw if isinstance(raw, StressScenario) else StressScenario.model_validate(raw)
    except ValidationError as error:
        detail = _safe_rejection_detail(f"scenario schema validation failed: {error}")
        return (
            ProposalRecord(
                round_number=round_number,
                candidate_number=candidate_number,
                scenario_id=scenario_id,
                input_sha256=input_sha256,
                semantic_sha256=None,
                decision=ProposalDecision.REJECTED,
                scenario=None,
                rejection_code=RejectionCode.INVALID_PARAMETER,
                rejection_detail=detail,
            ),
            None,
        )
    semantic_sha256 = _semantic_sha256(scenario)
    rejection: tuple[RejectionCode, str] | None = None
    if scenario.scenario_id in seen_ids:
        rejection = (RejectionCode.DUPLICATE_SCENARIO, "duplicate scenario_id")
    elif semantic_sha256 in seen_semantics:
        rejection = (
            RejectionCode.DUPLICATE_SCENARIO,
            "equivalent canonical numeric scenario payload",
        )
    seen_ids.add(scenario.scenario_id)
    seen_semantics.add(semantic_sha256)
    if rejection is None:
        try:
            policy.validate_scenario(scenario, context=validation_context)
        except AttackPolicyViolation as error:
            rejection = (RejectionCode.INVALID_PARAMETER, f"attack policy rejected: {error}")
    if rejection is not None:
        code, detail = rejection
        return (
            ProposalRecord(
                round_number=round_number,
                candidate_number=candidate_number,
                scenario_id=scenario.scenario_id,
                input_sha256=input_sha256,
                semantic_sha256=semantic_sha256,
                decision=ProposalDecision.REJECTED,
                scenario=scenario,
                rejection_code=code,
                rejection_detail=detail,
            ),
            None,
        )
    return (
        ProposalRecord(
            round_number=round_number,
            candidate_number=candidate_number,
            scenario_id=scenario.scenario_id,
            input_sha256=input_sha256,
            semantic_sha256=semantic_sha256,
            decision=ProposalDecision.ACCEPTED,
            scenario=scenario,
        ),
        scenario,
    )


def _linked_window_evidence(
    backtest: BacktestResult,
    breach_index: int,
) -> WorstWindowEvidence:
    breach = backtest.failure_evaluation.breaches[breach_index]
    index = backtest.portfolio_returns.index
    start = pd.Timestamp(breach.worst_window_start).tz_localize("UTC")
    end = pd.Timestamp(breach.worst_window_end).tz_localize("UTC")
    start_position = index.get_loc(start)
    end_position = index.get_loc(end)
    if not isinstance(start_position, int) or not isinstance(end_position, int):
        raise AttackValidationError("breach windows must resolve to unique market rows")
    first_position = (
        start_position + 1 if breach.family.value == "maximum_drawdown" else start_position
    )
    if first_position > end_position:
        raise AttackValidationError("breach worst window contains no earned return")
    row_slice = slice(first_position, end_position + 1)
    returns = backtest.portfolio_returns.iloc[row_slice].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    gross = 1.0 + returns
    after = np.ones(len(returns), dtype=np.float64)
    if len(returns) > 1:
        after[:-1] = np.cumprod(gross[:0:-1])[::-1]
    asset_values = backtest.asset_contributions.iloc[row_slice].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    linked_assets = np.sum(asset_values * after[:, None], axis=0)
    linked_cost = float(
        np.sum(
            -backtest.transaction_costs.iloc[row_slice].to_numpy(
                dtype=np.float64,
                copy=False,
            )
            * after
        )
    )
    compounded_return = float(np.prod(gross) - 1.0)
    weights = backtest.effective_weights.iloc[row_slice].mean(axis="index")
    return WorstWindowEvidence(
        rule_id=breach.rule_id,
        start_date=breach.worst_window_start,
        end_date=breach.worst_window_end,
        portfolio_compounded_return=compounded_return,
        asset_return_contributions={
            Symbol.SPY: float(linked_assets[0]),
            Symbol.TLT: float(linked_assets[1]),
        },
        transaction_cost_return_contribution=linked_cost,
        average_effective_weights={
            Symbol.SPY: float(weights.iloc[0]),
            Symbol.TLT: float(weights.iloc[1]),
        },
    )


def validate_scenario_runtime_admissibility(
    *,
    dataset: StoredDataset,
    baseline_asset_returns: pd.DataFrame,
    scenario: StressScenario,
    experiment: ExperimentSpec,
) -> StressTransformResult:
    """Run the shared pre-backtest checks required for any meaningful evaluation."""
    transform = apply_stress_scenario(
        baseline_asset_returns,
        scenario,
        experiment.transaction_cost_bps,
        experiment.numeric_tolerance,
        experiment.seed,
    )
    validate_supplied_asset_returns(dataset, transform.stressed_asset_returns)
    return transform


def _evaluate_scenario(
    *,
    dataset: StoredDataset,
    strategy: Strategy,
    experiment: ExperimentSpec,
    baseline: BacktestResult,
    scenario: StressScenario,
    round_number: int,
    candidate_number: int,
    config_sha256: str,
) -> ScenarioEvaluationRecord:
    input_sha256 = canonical_json_sha256(scenario)
    try:
        transform = validate_scenario_runtime_admissibility(
            dataset=dataset,
            baseline_asset_returns=baseline.asset_returns,
            scenario=scenario,
            experiment=experiment,
        )
        stressed = run_backtest_with_asset_returns(
            dataset,
            strategy,
            transform.stressed_asset_returns,
            transform.transaction_cost_bps_after,
            experiment.numeric_tolerance,
            experiment.failure_rules,
            baseline.portfolio_returns,
        )
    except StressWindowError as error:
        rejection_code = RejectionCode.INVALID_WINDOW
        rejection_detail = str(error)
    except (StressArithmeticError, StressCorrelationError, StressValidationError) as error:
        rejection_code = RejectionCode.INVALID_PARAMETER
        rejection_detail = str(error)
    except (StressTransformError, BacktestError) as error:
        rejection_code = RejectionCode.INVALID_DATA
        rejection_detail = str(error)
    else:
        breaches = stressed.failure_evaluation.breaches
        maximum_excess = max(
            (breach.normalized_excess for breach in breaches),
            default=0.0,
        )
        total_excess = math.fsum(breach.normalized_excess for breach in breaches)
        metrics = stressed.metrics
        worst_loss = max(
            metrics.maximum_drawdown,
            max(-metrics.total_return, 0.0),
            max(-metrics.worst_rolling_20_day_return, 0.0),
        )
        result = StressResult(
            experiment_id=experiment.experiment_id,
            scenario_id=scenario.scenario_id,
            dataset_id=dataset.manifest.dataset_id,
            strategy_id=strategy.spec.strategy_id,
            input_sha256=input_sha256,
            config_sha256=config_sha256,
            data_sha256=dataset.manifest.sha256,
            code_version=experiment.code_version,
            engine_version=f"{ATTACK_RUNNER_VERSION}+{STRESS_TRANSFORM_VERSION}",
            status=ResultStatus.VALID,
            metrics=metrics,
            breaches=breaches,
            breach_count=len(breaches),
            maximum_normalized_excess=maximum_excess,
            total_normalized_excess=total_excess,
            worst_portfolio_loss=worst_loss,
        )
        windows = tuple(
            _linked_window_evidence(stressed, breach_index) for breach_index in range(len(breaches))
        )
        chart_points = tuple(
            PerformanceChartPoint(
                date=timestamp.date(),
                baseline_equity=float(baseline.equity_curve.loc[timestamp]),
                stressed_equity=float(stressed.equity_curve.loc[timestamp]),
                stressed_drawdown=float(stressed.drawdown_curve.loc[timestamp]),
            )
            for timestamp in baseline.equity_curve.index
        )
        return ScenarioEvaluationRecord(
            round_number=round_number,
            candidate_number=candidate_number,
            scenario=scenario,
            result=result,
            transform_sha256=transform.canonical_sha256(),
            component_summaries=transform.component_summaries,
            pre_transform_summary=transform.pre_transform_summary,
            post_transform_summary=transform.post_transform_summary,
            worst_windows=windows,
            chart_points=chart_points,
        )
    rejected = _rejected_result(
        experiment=experiment,
        dataset=dataset,
        strategy=strategy,
        scenario_id=scenario.scenario_id,
        input_sha256=input_sha256,
        config_sha256=config_sha256,
        rejection_code=rejection_code,
        rejection_detail=rejection_detail,
    )
    return ScenarioEvaluationRecord(
        round_number=round_number,
        candidate_number=candidate_number,
        scenario=scenario,
        result=rejected,
    )


def evaluate_scenario(
    *,
    dataset: StoredDataset,
    strategy: Strategy,
    experiment: ExperimentSpec,
    baseline: BacktestResult,
    scenario: StressScenario,
    round_number: int,
    candidate_number: int,
    config_sha256: str,
) -> ScenarioEvaluationRecord:
    """Expose one deterministic evaluation for independent bounded replay."""
    return _evaluate_scenario(
        dataset=dataset,
        strategy=strategy,
        experiment=experiment,
        baseline=baseline,
        scenario=scenario,
        round_number=round_number,
        candidate_number=candidate_number,
        config_sha256=config_sha256,
    )


def _severity_key(record: ScenarioEvaluationRecord) -> tuple[float | str, ...]:
    result = record.result
    return (
        -result.breach_count,
        -result.maximum_normalized_excess,
        -result.total_normalized_excess,
        -result.worst_portfolio_loss,
        result.scenario_id,
    )


def _rank_top_failures(
    evaluations: Sequence[ScenarioEvaluationRecord],
    top_k: int,
) -> tuple[ScenarioEvaluationRecord, ...]:
    failures = filter(
        lambda record: (
            record.result.status is ResultStatus.VALID and record.result.breach_count > 0
        ),
        evaluations,
    )
    selected = sorted(failures, key=_severity_key)[:top_k]
    ranked: list[ScenarioEvaluationRecord] = []
    for rank, record in enumerate(selected, start=1):
        result_payload = record.result.model_dump(mode="python")
        result_payload["rank"] = rank
        ranked.append(
            record.model_copy(
                update={"result": StressResult.model_validate(result_payload)},
            )
        )
    return tuple(ranked)


def _validate_attack_context(
    dataset: StoredDataset,
    strategy: Strategy,
    experiment: ExperimentSpec,
) -> str:
    if experiment.dataset_id != dataset.manifest.dataset_id:
        raise AttackValidationError("experiment dataset_id does not match the dataset")
    if experiment.data_sha256 != dataset.manifest.sha256:
        raise AttackValidationError("experiment data_sha256 does not match the dataset")
    if experiment.strategy != strategy.spec:
        raise AttackValidationError("experiment strategy does not match the supplied strategy")
    manifest_sha256 = hashlib.sha256(canonical_manifest_bytes(dataset.manifest)).hexdigest()
    if manifest_sha256 != dataset.manifest_sha256:
        raise AttackValidationError("stored manifest hash does not match canonical manifest bytes")
    return manifest_sha256


def build_attack_validation_context(
    *,
    dataset: StoredDataset,
    strategy: Strategy,
    experiment: ExperimentSpec,
    baseline: BacktestResult,
) -> AttackValidationContext:
    """Build the sole authoritative policy-validation context for one attack run."""
    rebalance_dates = (
        tuple(timestamp.date() for timestamp in strategy.rebalance_dates(dataset))
        if isinstance(strategy, FixedMonthly6040Strategy)
        else ()
    )
    positive_turnover_dates = frozenset(
        timestamp.date() for timestamp in baseline.turnover.index[baseline.turnover.gt(0.0)]
    )
    return AttackValidationContext(
        strategy_spec=strategy.spec,
        market_dates=tuple(timestamp.date() for timestamp in dataset.data.index),
        rebalance_dates=rebalance_dates,
        transaction_cost_bps=experiment.transaction_cost_bps,
        positive_turnover_dates=positive_turnover_dates,
    )


def run_attack(
    *,
    dataset: StoredDataset,
    strategy: Strategy,
    experiment: ExperimentSpec,
    policy: AttackPolicy,
    proposer: CandidateProposer,
    artifact_directory: Path,
    clock: Clock = time.monotonic,
) -> AttackRun:
    """Run at most three bounded rounds and atomically publish the evidence bundle."""
    manifest_sha256 = _validate_attack_context(dataset, strategy, experiment)
    policy = policy.for_strategy(strategy.spec)
    config_sha256 = canonical_json_sha256(experiment)
    policy_sha256 = canonical_json_sha256(policy)
    budget = AttackBudget.from_experiment(experiment, clock)
    baseline = run_backtest(
        dataset,
        strategy,
        experiment.transaction_cost_bps,
        experiment.numeric_tolerance,
    )
    validation_context = build_attack_validation_context(
        dataset=dataset,
        strategy=strategy,
        experiment=experiment,
        baseline=baseline,
    )

    proposals: list[ProposalRecord] = []
    evaluations: list[ScenarioEvaluationRecord] = []
    prior_results: list[StressResult] = []
    seen_ids: set[str] = set()
    seen_semantics: set[str] = set()
    evaluated_scenarios = 0
    rejected_scenarios = 0
    qualifying_scenarios = 0
    stop_reason: StopReason | None = None

    for round_number in range(1, budget.max_rounds + 1):
        if budget.deadline_reached():
            stop_reason = StopReason.TIMEOUT
            break
        if budget.remaining_scenarios == 0:
            stop_reason = StopReason.MAX_TOTAL_SCENARIOS_REACHED
            break
        budget.start_round()
        maximum_batch = min(
            budget.max_candidates_per_round,
            budget.remaining_scenarios,
        )
        candidates = proposer.propose(
            round_number=round_number,
            max_candidates=maximum_batch,
            prior_results=tuple(prior_results),
        )
        if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
            raise AttackValidationError("proposer output must be a finite sequence")
        if len(candidates) > maximum_batch:
            stop_reason = StopReason.PROPOSER_BUDGET_VIOLATION
            break
        if not candidates:
            stop_reason = StopReason.PROPOSER_EXHAUSTED
            break
        budget.reserve_batch(len(candidates))
        timed_out = budget.deadline_reached()

        for candidate_number, raw in enumerate(candidates, start=1):
            proposal, scenario = _prepare_candidate(
                raw,
                round_number=round_number,
                candidate_number=candidate_number,
                policy=policy,
                validation_context=validation_context,
                seen_ids=seen_ids,
                seen_semantics=seen_semantics,
            )
            proposals.append(proposal)
            if scenario is None:
                rejected_scenarios += 1
                evaluations.append(
                    ScenarioEvaluationRecord(
                        round_number=round_number,
                        candidate_number=candidate_number,
                        scenario=proposal.scenario,
                        result=_rejected_result(
                            experiment=experiment,
                            dataset=dataset,
                            strategy=strategy,
                            scenario_id=proposal.scenario_id,
                            input_sha256=proposal.input_sha256,
                            config_sha256=config_sha256,
                            rejection_code=(
                                proposal.rejection_code or RejectionCode.INVALID_PARAMETER
                            ),
                            rejection_detail=(proposal.rejection_detail or "Candidate rejected."),
                        ),
                    )
                )
                timed_out = timed_out or budget.deadline_reached()
                continue
            if timed_out or budget.deadline_reached():
                timed_out = True
                rejected_scenarios += 1
                evaluations.append(
                    ScenarioEvaluationRecord(
                        round_number=round_number,
                        candidate_number=candidate_number,
                        scenario=scenario,
                        result=_rejected_result(
                            experiment=experiment,
                            dataset=dataset,
                            strategy=strategy,
                            scenario_id=scenario.scenario_id,
                            input_sha256=proposal.input_sha256,
                            config_sha256=config_sha256,
                            rejection_code=RejectionCode.TIMEOUT,
                            rejection_detail=(
                                "Scenario was not evaluated because the wall-clock deadline "
                                "was reached."
                            ),
                        ),
                    )
                )
                continue
            evaluation = _evaluate_scenario(
                dataset=dataset,
                strategy=strategy,
                experiment=experiment,
                baseline=baseline,
                scenario=scenario,
                round_number=round_number,
                candidate_number=candidate_number,
                config_sha256=config_sha256,
            )
            if budget.deadline_reached():
                timed_out = True
                rejected_scenarios += 1
                evaluations.append(
                    ScenarioEvaluationRecord(
                        round_number=round_number,
                        candidate_number=candidate_number,
                        scenario=scenario,
                        result=_rejected_result(
                            experiment=experiment,
                            dataset=dataset,
                            strategy=strategy,
                            scenario_id=scenario.scenario_id,
                            input_sha256=proposal.input_sha256,
                            config_sha256=config_sha256,
                            rejection_code=RejectionCode.TIMEOUT,
                            rejection_detail=(
                                "Scenario evidence was discarded because its evaluation "
                                "completed after the wall-clock deadline."
                            ),
                        ),
                    )
                )
                continue
            evaluations.append(evaluation)
            if evaluation.result.status is ResultStatus.VALID:
                evaluated_scenarios += 1
                prior_results.append(evaluation.result)
                if policy.evidence_condition.qualifies(evaluation.result):
                    qualifying_scenarios += 1
            else:
                rejected_scenarios += 1

        if timed_out:
            stop_reason = StopReason.TIMEOUT
            break
        if qualifying_scenarios >= policy.evidence_condition.minimum_failure_scenarios:
            stop_reason = StopReason.EVIDENCE_CONDITION_MET
            break
        if budget.remaining_scenarios == 0:
            stop_reason = StopReason.MAX_TOTAL_SCENARIOS_REACHED
            break

    if stop_reason is None:
        stop_reason = StopReason.MAX_ROUNDS_REACHED

    top_failures = _rank_top_failures(evaluations, budget.top_k)
    evidence_condition_met = (
        qualifying_scenarios >= policy.evidence_condition.minimum_failure_scenarios
    )
    attack_completed = stop_reason not in {
        StopReason.TIMEOUT,
        StopReason.PROPOSER_BUDGET_VIOLATION,
    }
    run = AttackRun(
        experiment=experiment,
        dataset_manifest=dataset.manifest,
        dataset_manifest_sha256=manifest_sha256,
        policy=policy,
        config_sha256=config_sha256,
        policy_sha256=policy_sha256,
        baseline_metrics=baseline.metrics,
        proposals=tuple(proposals),
        evaluations=tuple(evaluations),
        top_failures=top_failures,
        rounds_started=budget.rounds_started,
        candidate_slots_consumed=budget.candidate_slots_consumed,
        evaluated_scenarios=evaluated_scenarios,
        rejected_scenarios=rejected_scenarios,
        stop_reason=stop_reason,
        evidence_condition_met=evidence_condition_met,
        attack_completed=attack_completed,
    )
    from strategy_redteam.artifacts import write_run_artifacts

    write_run_artifacts(artifact_directory, run)
    return run
