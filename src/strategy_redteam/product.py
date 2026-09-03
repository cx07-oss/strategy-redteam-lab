"""MVP 3 product contracts around the deterministic research engine.

AI providers may propose only these typed hypotheses.  This module converts a
validated proposal into an existing stress scenario and lets the deterministic
engine alone decide whether the declared degradation threshold was reproduced.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from strategy_redteam.backtest import run_backtest
from strategy_redteam.data import StoredDataset
from strategy_redteam.domain import (
    ContractModel,
    ExperimentSpec,
    StressComponent,
    StressFamily,
    StressScenario,
    Symbol,
)
from strategy_redteam.research import ExperimentResult, PerformanceMetrics, performance_metrics
from strategy_redteam.strategy import strategy_from_spec
from strategy_redteam.stress import StressValidationError, run_stressed_backtest

MAX_AI_HYPOTHESES = 8


class AIProviderMode(StrEnum):
    DETERMINISTIC = "deterministic"
    LOCAL = "local"
    LIVE = "live"


class VerificationStatus(StrEnum):
    REPRODUCED = "reproduced"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"


class FailureMetric(StrEnum):
    TOTAL_RETURN = "total_return"
    MAXIMUM_DRAWDOWN = "maximum_drawdown"
    ANNUALIZED_VOLATILITY = "annualized_volatility"


SupportedAIFamily = Literal[
    StressFamily.VOLATILITY_MULTIPLIER,
    StressFamily.CORRELATION_TARGET,
    StressFamily.TRANSACTION_COST_MULTIPLIER,
]


class ProposedStressParameters(ContractModel):
    start_date: date | None = None
    end_date: date | None = None
    symbols: tuple[Symbol, ...] | None = None
    volatility_multiplier: float | None = Field(default=None, gt=0.0, le=3.0)
    target_correlation: float | None = Field(default=None, gt=-1.0, lt=1.0)
    transaction_cost_multiplier: float | None = Field(default=None, ge=1.0, le=5.0)


class AIHypothesis(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    hypothesis_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=64)
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=1000)
    targeted_vulnerability: str = Field(min_length=1, max_length=300)
    supported_stress_family: SupportedAIFamily
    proposed_parameters: ProposedStressParameters
    expected_failure_mechanism: str = Field(min_length=1, max_length=500)
    failure_metric: FailureMetric
    minimum_degradation: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def parameters_match_family(self) -> AIHypothesis:
        params = self.proposed_parameters
        dated = params.start_date is not None and params.end_date is not None
        if self.supported_stress_family is StressFamily.VOLATILITY_MULTIPLIER:
            if not dated or params.symbols != (Symbol.SPY, Symbol.TLT):
                raise ValueError("volatility hypotheses require a SPY/TLT date window")
            if params.volatility_multiplier is None:
                raise ValueError("volatility hypotheses require volatility_multiplier")
        elif self.supported_stress_family is StressFamily.CORRELATION_TARGET:
            if not dated or params.target_correlation is None:
                raise ValueError("correlation hypotheses require a date window and target")
        elif params.transaction_cost_multiplier is None:
            raise ValueError("cost hypotheses require transaction_cost_multiplier")
        used = sum(
            value is not None
            for value in (
                params.volatility_multiplier,
                params.target_correlation,
                params.transaction_cost_multiplier,
            )
        )
        if used != 1:
            raise ValueError("exactly one supported stress parameter is required")
        return self


class HypothesisBatch(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    hypotheses: tuple[AIHypothesis, ...] = Field(min_length=1, max_length=MAX_AI_HYPOTHESES)

    @model_validator(mode="after")
    def unique_hypotheses(self) -> HypothesisBatch:
        ids = [item.hypothesis_id for item in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("hypothesis_id values must be unique")
        return self


class VerificationMetrics(ContractModel):
    total_return: float
    sharpe_ratio: float | None
    maximum_drawdown: float | None
    annualized_volatility: float | None

    @classmethod
    def from_performance(cls, metrics: PerformanceMetrics) -> VerificationMetrics:
        return cls(
            total_return=metrics.total_return,
            sharpe_ratio=metrics.sharpe_ratio,
            maximum_drawdown=metrics.maximum_drawdown,
            annualized_volatility=metrics.annualized_volatility,
        )


class VerifiedHypothesis(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    hypothesis: AIHypothesis
    verification_status: VerificationStatus
    executed_stress_configuration: StressScenario | None
    baseline_metrics: VerificationMetrics
    stressed_metrics: VerificationMetrics | None
    observed_degradation: float | None
    evidence: str
    rejection_reason: str | None = None


class ProviderResult(ContractModel):
    provider_mode: AIProviderMode
    provider_identifier: str
    fallback_used: bool = False
    batch: HypothesisBatch


class HistoricalEvent(ContractModel):
    event_id: str
    label: str
    start_date: date
    end_date: date
    description: str
    explanatory_only: Literal[True] = True


class CanonicalProductArtifact(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    research: ExperimentResult
    ai_provider: ProviderResult
    ai_findings: tuple[VerifiedHypothesis, ...]
    historical_events: tuple[HistoricalEvent, ...]
    software_version: str


HISTORICAL_EVENTS: tuple[HistoricalEvent, ...] = (
    HistoricalEvent(
        event_id="gfc",
        label="Global Financial Crisis",
        start_date=date(2007, 8, 1),
        end_date=date(2009, 6, 30),
        description="Context marker only; never used to fit or label the GMM.",
    ),
    HistoricalEvent(
        event_id="covid-shock",
        label="COVID shock",
        start_date=date(2020, 2, 19),
        end_date=date(2020, 3, 23),
        description="Context marker only; never used to fit or label the GMM.",
    ),
    HistoricalEvent(
        event_id="inflation-rate-shock",
        label="Inflation / rate shock",
        start_date=date(2022, 1, 3),
        end_date=date(2022, 12, 30),
        description="Context marker only; never used to fit or label the GMM.",
    ),
)


def build_canonical_product_artifact(
    dataset: StoredDataset,
    spec: ExperimentSpec,
    research: ExperimentResult,
    *,
    transaction_cost_bps: float,
) -> CanonicalProductArtifact:
    if research.data_manifest.sha256 != dataset.manifest.sha256 or research.seed != spec.seed:
        raise ValueError("research artifact provenance does not match dataset/configuration")
    provider = DeterministicHypothesisProvider()
    batch = provider.propose(dataset.data.index[1].date(), dataset.data.index[-1].date())
    findings = verify_hypotheses(
        dataset,
        spec,
        batch.hypotheses,
        transaction_cost_bps=transaction_cost_bps,
    )
    return CanonicalProductArtifact(
        research=research,
        ai_provider=ProviderResult(
            provider_mode=provider.mode,
            provider_identifier=provider.identifier,
            batch=batch,
        ),
        ai_findings=findings,
        historical_events=HISTORICAL_EVENTS,
        software_version=spec.code_version,
    )


class DeterministicHypothesisProvider:
    """Zero-cost provider for CI, demos, and public execution."""

    mode = AIProviderMode.DETERMINISTIC
    identifier = "deterministic-mvp3-v1"

    def propose(self, start_date: date, end_date: date) -> HypothesisBatch:
        return HypothesisBatch(
            hypotheses=(
                AIHypothesis(
                    hypothesis_id="correlation-break",
                    title="Equity and duration diversification can fail together",
                    rationale=(
                        "A positive stock/bond correlation removes the portfolio's main "
                        "diversification assumption."
                    ),
                    targeted_vulnerability="cross-asset correlation concentration",
                    supported_stress_family=StressFamily.CORRELATION_TARGET,
                    proposed_parameters=ProposedStressParameters(
                        start_date=start_date,
                        end_date=end_date,
                        target_correlation=0.75,
                    ),
                    expected_failure_mechanism=(
                        "Simultaneous sleeve losses deepen portfolio drawdown."
                    ),
                    failure_metric=FailureMetric.MAXIMUM_DRAWDOWN,
                    minimum_degradation=0.01,
                ),
                AIHypothesis(
                    hypothesis_id="volatility-jump",
                    title="A volatility regime jump can overwhelm fixed weights",
                    rationale=(
                        "Static weights do not reduce exposure when both sleeves become "
                        "more volatile."
                    ),
                    targeted_vulnerability="fixed exposure during volatility expansion",
                    supported_stress_family=StressFamily.VOLATILITY_MULTIPLIER,
                    proposed_parameters=ProposedStressParameters(
                        start_date=start_date,
                        end_date=end_date,
                        symbols=(Symbol.SPY, Symbol.TLT),
                        volatility_multiplier=1.75,
                    ),
                    expected_failure_mechanism=(
                        "Larger daily moves increase realized volatility and tail loss."
                    ),
                    failure_metric=FailureMetric.ANNUALIZED_VOLATILITY,
                    minimum_degradation=0.02,
                ),
                AIHypothesis(
                    hypothesis_id="cost-fragility",
                    title="Execution costs can erode monthly rebalancing returns",
                    rationale=(
                        "Repeated turnover makes the net result sensitive to implementation "
                        "friction."
                    ),
                    targeted_vulnerability="turnover and execution costs",
                    supported_stress_family=StressFamily.TRANSACTION_COST_MULTIPLIER,
                    proposed_parameters=ProposedStressParameters(transaction_cost_multiplier=4.0),
                    expected_failure_mechanism="Higher costs reduce the terminal net return.",
                    failure_metric=FailureMetric.TOTAL_RETURN,
                    minimum_degradation=0.002,
                ),
            )
        )


def propose_with_fallback(
    provider_mode: AIProviderMode,
    provider_identifier: str,
    raw_proposer: Callable[[], str],
    start_date: date,
    end_date: date,
) -> ProviderResult:
    """Validate untrusted provider JSON and fall back without executing its content."""
    try:
        raw = raw_proposer()
        if len(raw.encode("utf-8")) > 262_144:
            raise ValueError("provider output exceeds the bounded response size")
        batch = HypothesisBatch.model_validate_json(raw)
        return ProviderResult(
            provider_mode=provider_mode,
            provider_identifier=provider_identifier,
            batch=batch,
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        fallback = DeterministicHypothesisProvider()
        return ProviderResult(
            provider_mode=fallback.mode,
            provider_identifier=fallback.identifier,
            fallback_used=True,
            batch=fallback.propose(start_date, end_date),
        )


def _scenario(hypothesis: AIHypothesis, dataset: StoredDataset) -> StressScenario:
    params = hypothesis.proposed_parameters
    start = params.start_date or dataset.data.index[1].date()
    end = params.end_date or dataset.data.index[-1].date()
    component = StressComponent(
        family=hypothesis.supported_stress_family,
        start_date=params.start_date,
        end_date=params.end_date,
        symbols=params.symbols,
        volatility_multiplier=params.volatility_multiplier,
        target_correlation=params.target_correlation,
        transaction_cost_multiplier=params.transaction_cost_multiplier,
    )
    return StressScenario(
        scenario_id=f"ai-{hypothesis.hypothesis_id}",
        evaluation_start=start,
        evaluation_end=end,
        hypothesis=hypothesis.expected_failure_mechanism,
        components=(component,),
    )


def _metric(metrics: VerificationMetrics, name: FailureMetric) -> float:
    value = {
        FailureMetric.TOTAL_RETURN: metrics.total_return,
        FailureMetric.MAXIMUM_DRAWDOWN: metrics.maximum_drawdown,
        FailureMetric.ANNUALIZED_VOLATILITY: metrics.annualized_volatility,
    }[name]
    if value is None:
        raise ValueError(f"{name.value} is undefined")
    return value


def verify_hypotheses(
    dataset: StoredDataset,
    spec: ExperimentSpec,
    hypotheses: Sequence[AIHypothesis],
    *,
    transaction_cost_bps: float,
) -> tuple[VerifiedHypothesis, ...]:
    """Run existing stress/backtest code and derive all verdicts from its metrics."""
    verified: list[VerifiedHypothesis] = []
    for hypothesis in hypotheses[:MAX_AI_HYPOTHESES]:
        scenario: StressScenario | None = None
        baseline_metrics: VerificationMetrics | None = None
        try:
            scenario = _scenario(hypothesis, dataset)
            replay = run_stressed_backtest(
                dataset,
                strategy_from_spec(spec.strategy, spec.numeric_tolerance),
                scenario,
                transaction_cost_bps,
                spec.numeric_tolerance,
                spec.failure_rules,
                spec.seed,
            )
            baseline_metrics = VerificationMetrics.from_performance(
                performance_metrics(replay.baseline_backtest.portfolio_returns.iloc[1:])
            )
            stressed_metrics = VerificationMetrics.from_performance(
                performance_metrics(replay.stressed_backtest.portfolio_returns.iloc[1:])
            )
            before = _metric(baseline_metrics, hypothesis.failure_metric)
            after = _metric(stressed_metrics, hypothesis.failure_metric)
            degradation = (
                before - after
                if hypothesis.failure_metric is FailureMetric.TOTAL_RETURN
                else after - before
            )
            reproduced = degradation >= hypothesis.minimum_degradation
            verified.append(
                VerifiedHypothesis(
                    hypothesis=hypothesis,
                    verification_status=(
                        VerificationStatus.REPRODUCED if reproduced else VerificationStatus.REJECTED
                    ),
                    executed_stress_configuration=scenario,
                    baseline_metrics=baseline_metrics,
                    stressed_metrics=stressed_metrics,
                    observed_degradation=degradation,
                    evidence=(
                        f"Deterministic {hypothesis.failure_metric.value} degradation "
                        f"{degradation:.8f}; required {hypothesis.minimum_degradation:.8f}."
                    ),
                    rejection_reason=None if reproduced else "degradation threshold was not met",
                )
            )
        except (ValueError, StressValidationError) as error:
            if baseline_metrics is None:
                # A valid dataset/spec always reaches this point; keep unsupported
                # evidence typed if a provider proposes an inadmissible transform.
                baseline = run_backtest(
                    dataset,
                    strategy_from_spec(spec.strategy, spec.numeric_tolerance),
                    transaction_cost_bps,
                    spec.numeric_tolerance,
                )
                baseline_metrics = VerificationMetrics.from_performance(
                    performance_metrics(baseline.portfolio_returns.iloc[1:])
                )
            verified.append(
                VerifiedHypothesis(
                    hypothesis=hypothesis,
                    verification_status=VerificationStatus.UNSUPPORTED,
                    executed_stress_configuration=scenario,
                    baseline_metrics=baseline_metrics,
                    stressed_metrics=None,
                    observed_degradation=None,
                    evidence="The deterministic engine did not execute this proposal.",
                    rejection_reason=str(error),
                )
            )
    return tuple(verified)
