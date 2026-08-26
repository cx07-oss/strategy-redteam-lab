"""Local attacker/defender application boundaries with untrusted JSON clients.

Model-facing clients receive bounded typed summaries. They never receive a dataset
path, artifact path, callable, or full daily price history. Deterministic code owns
scenario validation, evaluation, replay, comparisons, and every report number.
"""

from __future__ import annotations

import hashlib
import html
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Protocol, Self

from pydantic import Field, ValidationError, model_validator

from strategy_redteam.attack import (
    AttackPolicy,
    AttackRun,
    CandidatePayload,
    Clock,
    ScenarioEvaluationRecord,
    canonical_json_sha256,
    evaluate_scenario,
    run_attack,
)
from strategy_redteam.backtest import BacktestError, BacktestResult, run_backtest
from strategy_redteam.data import (
    DatasetVerificationError,
    LocalDatasetStore,
    StoredDataset,
    canonical_manifest_bytes,
)
from strategy_redteam.domain import (
    MAX_CANDIDATES_PER_ROUND,
    MAX_ROUNDS,
    MAX_TOTAL_SCENARIOS,
    TOP_K,
    AttackBatch,
    ComponentTransformSummary,
    ContractModel,
    DefenderVerdict,
    DefenderVerdictValue,
    ExperimentSpec,
    FailureReport,
    FailureRule,
    Identifier,
    MetricSet,
    NarrativeText,
    NonNegativeFloat,
    ReturnSummary,
    SchemaVersion,
    Sha256,
    StrategySpec,
    StressComponent,
    StressFamily,
    StressResult,
    StressScenario,
    Symbol,
)
from strategy_redteam.strategy import (
    Strategy,
    StrategyError,
    close_prices,
    strategy_from_spec,
)
from strategy_redteam.stress import summarize_asset_returns

PROMPT_DIRECTORY = Path(__file__).resolve().parents[2] / "prompts"
ATTACKER_PROMPT_PATH = PROMPT_DIRECTORY / "attacker.md"
DEFENDER_PROMPT_PATH = PROMPT_DIRECTORY / "defender.md"
MAX_PROMPT_BYTES = 32_768
MAX_MODEL_RESPONSE_BYTES = 262_144
MAX_PRIOR_RESULT_SUMMARIES = MAX_TOTAL_SCENARIOS - MAX_CANDIDATES_PER_ROUND


class ApplicationBoundaryError(Exception):
    """Base error for local model-client application boundaries."""

    @property
    def safe_rejection_detail(self) -> str:
        """Return a bounded diagnostic suitable for an artifact; never model content."""
        return "proposer structured output validation failed"


class PromptTemplateError(ApplicationBoundaryError):
    """A fixed external prompt template is missing, invalid, or oversized."""


class FakeClientExhausted(ApplicationBoundaryError):
    """A deterministic fake has no configured response remaining."""


class DefenderServiceError(ApplicationBoundaryError):
    """The defender could not construct a typed bounded outcome."""


def _load_prompt(path: Path) -> str:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise PromptTemplateError(f"cannot read fixed prompt template: {path.name}") from error
    if not content or len(content) > MAX_PROMPT_BYTES:
        raise PromptTemplateError(
            f"prompt template must contain 1..{MAX_PROMPT_BYTES} bytes"
        )
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PromptTemplateError("prompt template must be UTF-8") from error


class PriorResultSummary(ContractModel):
    """Compact engine-owned feedback from an earlier bounded attack round."""

    schema_version: SchemaVersion = "1.0"
    scenario_id: Identifier
    breach_count: Annotated[int, Field(strict=True, ge=0, le=3)]
    maximum_normalized_excess: NonNegativeFloat
    total_normalized_excess: NonNegativeFloat
    worst_portfolio_loss: Annotated[
        float,
        Field(strict=True, ge=0.0, lt=1.0, allow_inf_nan=False),
    ]
    metrics: MetricSet


class AttackerEvidenceSummary(ContractModel):
    """Bounded model input containing statistics but no daily price history."""

    schema_version: SchemaVersion = "1.0"
    experiment_id: Identifier
    dataset_id: Identifier
    data_sha256: Sha256
    round_number: Annotated[int, Field(strict=True, ge=1, le=MAX_ROUNDS)]
    max_candidates: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_CANDIDATES_PER_ROUND),
    ]
    remaining_scenarios: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_TOTAL_SCENARIOS),
    ]
    seed: Annotated[int, Field(strict=True, ge=0, le=4_294_967_295)]
    strategy: StrategySpec
    transaction_cost_bps: Annotated[
        float,
        Field(strict=True, ge=0.0, lt=10_000.0, allow_inf_nan=False),
    ]
    market_summary: ReturnSummary
    failure_rules: tuple[FailureRule, ...] = Field(min_length=1, max_length=3)
    policy: AttackPolicy
    prior_results: tuple[PriorResultSummary, ...] = Field(
        default=(),
        max_length=MAX_PRIOR_RESULT_SUMMARIES,
    )

    @model_validator(mode="after")
    def validate_round_capacity(self) -> Self:
        if self.max_candidates > self.remaining_scenarios:
            raise ValueError("max_candidates exceeds the remaining scenario budget")
        previous_capacity = (self.round_number - 1) * MAX_CANDIDATES_PER_ROUND
        if len(self.prior_results) > previous_capacity:
            raise ValueError("prior_results exceeds the capacity of completed rounds")
        return self


class ScenarioProposer(Protocol):
    """JSON-only scenario client; it has no numerical or operational authority."""

    def propose(
        self,
        *,
        prompt: str,
        evidence_summary: AttackerEvidenceSummary,
    ) -> str:
        """Return one UTF-8-sized JSON string validating as ``AttackBatch``."""


@dataclass
class FakeScenarioProposer:
    """Deterministic response queue for every local attacker test."""

    responses: tuple[str | AttackBatch, ...]
    on_call: Callable[[], None] | None = None
    calls: list[AttackerEvidenceSummary] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    _position: int = 0

    def propose(
        self,
        *,
        prompt: str,
        evidence_summary: AttackerEvidenceSummary,
    ) -> str:
        self.prompts.append(prompt)
        self.calls.append(evidence_summary)
        if self.on_call is not None:
            self.on_call()
        if self._position >= len(self.responses):
            raise FakeClientExhausted("fake scenario proposer exhausted")
        response = self.responses[self._position]
        self._position += 1
        if isinstance(response, AttackBatch):
            return response.model_dump_json()
        return response


class _ScenarioProposerAdapter:
    """Convert untrusted batch JSON into the Gate 6 candidate boundary."""

    def __init__(
        self,
        *,
        proposer: ScenarioProposer,
        prompt: str,
        experiment: ExperimentSpec,
        market_summary: ReturnSummary,
        policy: AttackPolicy,
    ) -> None:
        self._proposer = proposer
        self._prompt = prompt
        self._experiment = experiment
        self._market_summary = market_summary
        self._policy = policy
        self._candidate_slots_returned = 0

    @staticmethod
    def _invalid_candidate(round_number: int, detail: str) -> Mapping[str, object]:
        return {
            "scenario_id": f"invalid-batch-r{round_number:02d}",
            "batch_validation_error": detail[:500],
        }

    def _summary(
        self,
        *,
        round_number: int,
        max_candidates: int,
        prior_results: tuple[StressResult, ...],
    ) -> AttackerEvidenceSummary:
        compact_results = tuple(
            PriorResultSummary(
                scenario_id=result.scenario_id,
                breach_count=result.breach_count,
                maximum_normalized_excess=result.maximum_normalized_excess,
                total_normalized_excess=result.total_normalized_excess,
                worst_portfolio_loss=result.worst_portfolio_loss,
                metrics=result.metrics,
            )
            for result in prior_results
            if result.metrics is not None
        )
        remaining = self._experiment.max_total_scenarios - self._candidate_slots_returned
        return AttackerEvidenceSummary(
            experiment_id=self._experiment.experiment_id,
            dataset_id=self._experiment.dataset_id,
            data_sha256=self._experiment.data_sha256,
            round_number=round_number,
            max_candidates=max_candidates,
            remaining_scenarios=remaining,
            seed=self._experiment.seed,
            strategy=self._experiment.strategy,
            transaction_cost_bps=self._experiment.transaction_cost_bps,
            market_summary=self._market_summary,
            failure_rules=self._experiment.failure_rules,
            policy=self._policy,
            prior_results=compact_results,
        )

    def propose(
        self,
        *,
        round_number: int,
        max_candidates: int,
        prior_results: tuple[StressResult, ...],
    ) -> Sequence[CandidatePayload]:
        summary = self._summary(
            round_number=round_number,
            max_candidates=max_candidates,
            prior_results=prior_results,
        )
        try:
            raw = self._proposer.propose(
                prompt=self._prompt,
                evidence_summary=summary,
            )
        except FakeClientExhausted:
            return ()
        except ApplicationBoundaryError as error:
            candidates = (
                self._invalid_candidate(
                    round_number, error.safe_rejection_detail
                ),
            )
            self._candidate_slots_returned += 1
            return candidates
        if not isinstance(raw, str):
            candidates = (
                self._invalid_candidate(round_number, "proposer response was not text"),
            )
            self._candidate_slots_returned += 1
            return candidates
        if len(raw.encode("utf-8")) > MAX_MODEL_RESPONSE_BYTES:
            candidates = (
                self._invalid_candidate(round_number, "proposer response exceeded byte limit"),
            )
            self._candidate_slots_returned += 1
            return candidates
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            candidates = (
                self._invalid_candidate(round_number, "malformed AttackBatch JSON"),
            )
            self._candidate_slots_returned += 1
            return candidates

        if isinstance(payload, dict):
            raw_scenarios = payload.get("scenarios")
            if isinstance(raw_scenarios, list) and len(raw_scenarios) > max_candidates:
                return tuple(raw_scenarios[: max_candidates + 1])
        try:
            batch = AttackBatch.model_validate_json(raw)
        except ValidationError:
            candidates = (
                self._invalid_candidate(round_number, "AttackBatch schema validation failed"),
            )
            self._candidate_slots_returned += 1
            return candidates
        if (
            batch.experiment_id != self._experiment.experiment_id
            or batch.round_number != round_number
        ):
            candidates = (
                self._invalid_candidate(round_number, "AttackBatch context did not match request"),
            )
            self._candidate_slots_returned += 1
            return candidates
        if len(batch.scenarios) > max_candidates:
            return batch.scenarios[: max_candidates + 1]
        self._candidate_slots_returned += len(batch.scenarios)
        return batch.scenarios


class AttackerService:
    """Own the fixed-budget propose, validate, and deterministic evaluate flow."""

    def __init__(
        self,
        proposer: ScenarioProposer,
        prompt: str | None = None,
    ) -> None:
        self.proposer = proposer
        self.prompt = _load_prompt(ATTACKER_PROMPT_PATH) if prompt is None else prompt

    def run(
        self,
        *,
        dataset: StoredDataset,
        strategy: Strategy,
        experiment: ExperimentSpec,
        policy: AttackPolicy,
        artifact_directory: Path,
        clock: Clock = time.monotonic,
    ) -> AttackRun:
        prices = close_prices(dataset)
        asset_returns = prices.pct_change(fill_method=None).fillna(0.0)
        asset_returns.columns.name = "symbol"
        runtime_policy = policy.for_strategy(experiment.strategy)
        market_summary = summarize_asset_returns(
            asset_returns,
            experiment.numeric_tolerance,
        )
        adapter = _ScenarioProposerAdapter(
            proposer=self.proposer,
            prompt=self.prompt,
            experiment=experiment,
            market_summary=market_summary,
            policy=runtime_policy,
        )
        return run_attack(
            dataset=dataset,
            strategy=strategy,
            experiment=experiment,
            policy=runtime_policy,
            proposer=adapter,
            artifact_directory=artifact_directory,
            clock=clock,
        )


class CausalClaimStatus(StrEnum):
    """Permitted audit labels for an attacker narrative claim."""

    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class CausalClaimAssessment(ContractModel):
    """A bounded narrative judgement with typed mechanisms and no report numbers."""

    schema_version: SchemaVersion = "1.0"
    scenario_id: Identifier
    status: CausalClaimStatus
    claimed_families: tuple[StressFamily, ...] = Field(default=(), max_length=5)
    reason: NarrativeText

    @model_validator(mode="after")
    def verified_claim_requires_a_mechanism(self) -> Self:
        if self.status is CausalClaimStatus.VERIFIED and not self.claimed_families:
            raise ValueError("verified causal claims require a typed mechanism")
        if len(set(self.claimed_families)) != len(self.claimed_families):
            raise ValueError("claimed_families must be unique")
        return self


class DefenderNarrativeBatch(ContractModel):
    """JSON-only report-writer output; it never supplies Markdown or metrics."""

    schema_version: SchemaVersion = "1.0"
    assessments: tuple[CausalClaimAssessment, ...] = Field(default=(), max_length=TOP_K)

    @model_validator(mode="after")
    def assessment_ids_must_be_unique(self) -> Self:
        identifiers = [assessment.scenario_id for assessment in self.assessments]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("assessment scenario IDs must be unique")
        return self


class DefenderEvidenceItem(ContractModel):
    """Bounded replay state plus explicitly untrusted attacker narrative."""

    schema_version: SchemaVersion = "1.0"
    scenario_id: Identifier
    replay_verdict: DefenderVerdictValue
    scenario_families: tuple[StressFamily, ...] = Field(min_length=1, max_length=5)
    breached_rule_ids: tuple[Identifier, ...] = Field(default=(), max_length=3)
    attacker_hypothesis_untrusted: NarrativeText
    attacker_headline_untrusted: str | None = Field(default=None, max_length=500)


class DefenderEvidenceSummary(ContractModel):
    """At most TOP_K replay outcomes supplied to the narrative-only writer."""

    schema_version: SchemaVersion = "1.0"
    experiment_id: Identifier
    items: tuple[DefenderEvidenceItem, ...] = Field(default=(), max_length=TOP_K)


class ReportWriter(Protocol):
    """Narrative audit client whose JSON cannot set paths or numeric evidence."""

    def write(
        self,
        *,
        prompt: str,
        evidence_summary: DefenderEvidenceSummary,
    ) -> str:
        """Return JSON validating as ``DefenderNarrativeBatch``."""


@dataclass
class FakeReportWriter:
    """Deterministic response queue for every local defender test."""

    responses: tuple[str | DefenderNarrativeBatch, ...]
    calls: list[DefenderEvidenceSummary] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    _position: int = 0

    def write(
        self,
        *,
        prompt: str,
        evidence_summary: DefenderEvidenceSummary,
    ) -> str:
        self.prompts.append(prompt)
        self.calls.append(evidence_summary)
        if self._position >= len(self.responses):
            raise FakeClientExhausted("fake report writer exhausted")
        response = self.responses[self._position]
        self._position += 1
        if isinstance(response, DefenderNarrativeBatch):
            return response.model_dump_json()
        return response


@dataclass(frozen=True)
class DefenseRun:
    """Typed defender output plus safe Markdown and narrative audit diagnostics."""

    verdicts: tuple[DefenderVerdict, ...]
    report: FailureReport
    markdown: str
    replay_records: tuple[ScenarioEvaluationRecord, ...]
    accepted_assessments: tuple[CausalClaimAssessment, ...]
    narrative_rejections: tuple[str, ...]


def _metric_delta(expected: MetricSet, replayed: MetricSet) -> float:
    differences = (
        abs(expected.total_return - replayed.total_return),
        abs(expected.maximum_drawdown - replayed.maximum_drawdown),
        abs(
            expected.worst_rolling_20_day_return
            - replayed.worst_rolling_20_day_return
        ),
        abs(expected.annualized_volatility - replayed.annualized_volatility),
        float(abs(expected.observation_count - replayed.observation_count)),
    )
    return max(differences)


def _event_signature(result: StressResult) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            breach.rule_id,
            breach.family,
            breach.onset_date,
            breach.worst_window_start,
            breach.worst_window_end,
            breach.trough_date,
            breach.recovery_date,
            breach.affected_symbols,
        )
        for breach in result.breaches
    )


def _breaches_match(expected: StressResult, replayed: StressResult, tolerance: float) -> bool:
    if len(expected.breaches) != len(replayed.breaches):
        return False
    for left, right in zip(expected.breaches, replayed.breaches, strict=True):
        exact_fields = (
            left.schema_version == right.schema_version,
            left.rule_id == right.rule_id,
            left.family is right.family,
            left.onset_date == right.onset_date,
            left.worst_window_start == right.worst_window_start,
            left.worst_window_end == right.worst_window_end,
            left.trough_date == right.trough_date,
            left.recovery_date == right.recovery_date,
            left.affected_symbols == right.affected_symbols,
        )
        numeric_deltas = (
            abs(left.observed_value - right.observed_value),
            abs(left.threshold - right.threshold),
            abs(left.normalized_excess - right.normalized_excess),
        )
        if not all(exact_fields) or max(numeric_deltas) > tolerance:
            return False
    return True


def _results_match(expected: StressResult, replayed: StressResult, tolerance: float) -> bool:
    expected_metrics = expected.metrics
    replayed_metrics = replayed.metrics
    if expected_metrics is None or replayed_metrics is None:
        return False
    exact_fields = (
        expected.schema_version == replayed.schema_version,
        expected.experiment_id == replayed.experiment_id,
        expected.scenario_id == replayed.scenario_id,
        expected.dataset_id == replayed.dataset_id,
        expected.strategy_id == replayed.strategy_id,
        expected.input_sha256 == replayed.input_sha256,
        expected.config_sha256 == replayed.config_sha256,
        expected.data_sha256 == replayed.data_sha256,
        expected.code_version == replayed.code_version,
        expected.engine_version == replayed.engine_version,
        expected.status is replayed.status,
        expected.historical_window == replayed.historical_window,
        expected.breach_count == replayed.breach_count,
        expected.rejection_code == replayed.rejection_code,
        expected.rejection_detail == replayed.rejection_detail,
    )
    ranking_deltas = (
        abs(
            expected.maximum_normalized_excess
            - replayed.maximum_normalized_excess
        ),
        abs(expected.total_normalized_excess - replayed.total_normalized_excess),
        abs(expected.worst_portfolio_loss - replayed.worst_portfolio_loss),
    )
    return (
        all(exact_fields)
        and _metric_delta(expected_metrics, replayed_metrics) <= tolerance
        and max(ranking_deltas) <= tolerance
        and _breaches_match(expected, replayed, tolerance)
    )


def _worst_windows_match(
    expected: ScenarioEvaluationRecord,
    replayed: ScenarioEvaluationRecord,
    tolerance: float,
) -> bool:
    if len(expected.worst_windows) != len(replayed.worst_windows):
        return False
    for left, right in zip(expected.worst_windows, replayed.worst_windows, strict=True):
        if (
            left.schema_version != right.schema_version
            or left.rule_id != right.rule_id
            or left.start_date != right.start_date
            or left.end_date != right.end_date
            or set(left.asset_return_contributions)
            != set(right.asset_return_contributions)
            or set(left.average_effective_weights)
            != set(right.average_effective_weights)
        ):
            return False
        numeric_deltas = [
            abs(left.portfolio_compounded_return - right.portfolio_compounded_return),
            abs(
                left.transaction_cost_return_contribution
                - right.transaction_cost_return_contribution
            ),
        ]
        numeric_deltas.extend(
            abs(left.asset_return_contributions[symbol] - right.asset_return_contributions[symbol])
            for symbol in left.asset_return_contributions
        )
        numeric_deltas.extend(
            abs(left.average_effective_weights[symbol] - right.average_effective_weights[symbol])
            for symbol in left.average_effective_weights
        )
        if max(numeric_deltas) > tolerance:
            return False
    return True


def _record_replay_matches(
    expected: ScenarioEvaluationRecord,
    replayed: ScenarioEvaluationRecord,
    tolerance: float,
) -> tuple[bool, bool, bool, float | None]:
    expected_metrics = expected.result.metrics
    replay_metrics = replayed.result.metrics
    if expected_metrics is None or replay_metrics is None:
        return False, False, False, None
    max_delta = _metric_delta(expected_metrics, replay_metrics)
    transform_matches = expected.transform_sha256 == replayed.transform_sha256
    event_dates_match = _event_signature(expected.result) == _event_signature(replayed.result)
    result_matches = _results_match(
        expected.result,
        replayed.result,
        tolerance,
    ) and _worst_windows_match(
        expected,
        replayed,
        tolerance,
    )
    return result_matches, event_dates_match, transform_matches, max_delta


def _scenario_families(scenario: StressScenario) -> tuple[StressFamily, ...]:
    return tuple(dict.fromkeys(component.family for component in scenario.components))


def _audit_narrative(
    *,
    writer: ReportWriter,
    prompt: str,
    evidence: DefenderEvidenceSummary,
) -> tuple[tuple[CausalClaimAssessment, ...], tuple[str, ...]]:
    try:
        raw = writer.write(prompt=prompt, evidence_summary=evidence)
    except FakeClientExhausted:
        return (), ("Report writer returned no bounded response.",)
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MODEL_RESPONSE_BYTES:
        return (), ("Report writer response violated the bounded text contract.",)
    try:
        draft = DefenderNarrativeBatch.model_validate_json(raw)
    except ValidationError:
        return (), ("Report writer response was not schema-valid JSON.",)
    item_by_id = {item.scenario_id: item for item in evidence.items}
    accepted: list[CausalClaimAssessment] = []
    rejections: list[str] = []
    for assessment in draft.assessments:
        item = item_by_id.get(assessment.scenario_id)
        if item is None:
            rejections.append("A causal assessment referenced an unknown scenario ID.")
            continue
        supported_families = set(item.scenario_families)
        if assessment.status is CausalClaimStatus.VERIFIED and (
            item.replay_verdict is not DefenderVerdictValue.REPRODUCED
            or not set(assessment.claimed_families).issubset(supported_families)
        ):
            rejections.append(
                f"Unsupported causal claim rejected for {assessment.scenario_id}."
            )
            continue
        accepted.append(assessment)
    supplied_ids = {assessment.scenario_id for assessment in draft.assessments}
    missing_ids = set(item_by_id) - supplied_ids
    if missing_ids:
        rejections.append("Report writer omitted one or more bounded scenario assessments.")
    return tuple(accepted), tuple(rejections)


def _stress_description(scenario: StressScenario) -> str:
    descriptions: list[str] = []
    for component in scenario.components:
        if component.family is StressFamily.ONE_DAY_GAP:
            shocks = ", ".join(
                f"{symbol.value} {value:+.6%}"
                for symbol, value in (component.shocks or {}).items()
            )
            descriptions.append(f"one-day gap on {component.date}: {shocks}")
        elif component.family is StressFamily.SUSTAINED_CUMULATIVE_SHOCK:
            shocks = ", ".join(
                f"{symbol.value} {value:+.6%}"
                for symbol, value in (component.shocks or {}).items()
            )
            descriptions.append(
                f"cumulative shock from {component.start_date} over "
                f"{component.duration_rows} rows: {shocks}"
            )
        elif component.family is StressFamily.VOLATILITY_MULTIPLIER:
            symbols = ", ".join(symbol.value for symbol in (component.symbols or ()))
            descriptions.append(
                f"{component.volatility_multiplier:.6f}x volatility for {symbols} from "
                f"{component.start_date} through {component.end_date}"
            )
        elif component.family is StressFamily.CORRELATION_TARGET:
            descriptions.append(
                f"SPY/TLT correlation target {component.target_correlation:.6f} from "
                f"{component.start_date} through {component.end_date}"
            )
        elif component.family is StressFamily.TRANSACTION_COST_MULTIPLIER:
            descriptions.append(
                f"{component.transaction_cost_multiplier:.6f}x transaction costs"
            )
    return "; then ".join(descriptions)


def _fixed_explanation(
    record: ScenarioEvaluationRecord,
    accepted: Mapping[str, CausalClaimAssessment],
) -> str:
    scenario = record.scenario
    if scenario is None:
        raise DefenderServiceError("verified replay has no typed scenario")
    mechanism_labels = {
        StressFamily.ONE_DAY_GAP: "resilience to abrupt common losses",
        StressFamily.SUSTAINED_CUMULATIVE_SHOCK: "resilience to persistent common losses",
        StressFamily.VOLATILITY_MULTIPLIER: "stable risk under higher return dispersion",
        StressFamily.CORRELATION_TARGET: "negative equity-bond diversification",
        StressFamily.TRANSACTION_COST_MULTIPLIER: "the configured execution-cost regime",
    }
    families = _scenario_families(scenario)
    assumptions = ", ".join(mechanism_labels[family] for family in families)
    transformations = " ".join(
        _component_causal_change(component, summary)
        for component, summary in zip(
            scenario.components,
            record.component_summaries,
            strict=True,
        )
    )
    propagation = ""
    if record.result.breaches and record.worst_windows:
        breach = record.result.breaches[0]
        window = record.worst_windows[0]
        spy = window.asset_return_contributions[Symbol.SPY]
        tlt = window.asset_return_contributions[Symbol.TLT]
        if spy < 0.0 and tlt < 0.0:
            offset = "Both sleeves contributed negatively, so neither offset the loss."
        elif spy < 0.0 <= tlt:
            offset = "TLT offset part of the negative SPY contribution, but not enough."
        elif tlt < 0.0 <= spy:
            offset = "SPY offset part of the negative TLT contribution, but not enough."
        else:
            offset = "The linked transaction-cost contribution drove the negative window."
        propagation = (
            f" In the engine-identified worst window, SPY contributed {spy:+.6%} and "
            f"TLT contributed {tlt:+.6%}. {offset} The linked portfolio return was "
            f"{window.portfolio_compounded_return:+.6%}; `{breach.rule_id}` "
            f"({breach.family.value}) first breached on {breach.onset_date}."
        )
    assessment = accepted.get(record.result.scenario_id)
    if assessment is None or assessment.status is not CausalClaimStatus.VERIFIED:
        qualification = "Any broader attacker causal narrative remains unverifiable."
    else:
        qualification = "The causal label is limited to those explicit typed mechanisms."
    return (
        f"The challenged assumptions were {assumptions}. {transformations}{propagation} "
        f"Independent deterministic replay reproduced this chain. {qualification}"
    )


def _component_causal_change(
    component: StressComponent,
    summary: ComponentTransformSummary,
) -> str:
    """Describe only pre/post numbers stored in replayed transform evidence."""
    before = summary.pre_transform_summary
    after = summary.post_transform_summary
    before_assets = {asset.symbol: asset for asset in before.assets}
    after_assets = {asset.symbol: asset for asset in after.assets}
    if component.family is StressFamily.VOLATILITY_MULTIPLIER:
        changes: list[str] = []
        for symbol in (Symbol.SPY, Symbol.TLT):
            pre_std = before_assets[symbol].sample_log_return_std
            post_std = after_assets[symbol].sample_log_return_std
            if pre_std is not None and post_std is not None:
                changes.append(
                    f"{symbol.value} sample log-return volatility changed from "
                    f"{pre_std:.6f} to {post_std:.6f}"
                )
        return "; ".join(changes) + "."
    if component.family is StressFamily.CORRELATION_TARGET:
        pre_correlation = before.spy_tlt_correlation
        post_correlation = after.spy_tlt_correlation
        if pre_correlation is None or post_correlation is None:
            return "The SPY/TLT innovation correlation was non-evaluable."
        if pre_correlation < 0.0 < post_correlation:
            return (
                "SPY/TLT innovation correlation became positive, changing from "
                f"{pre_correlation:.6f} to {post_correlation:.6f}."
            )
        return (
            "SPY/TLT innovation correlation changed from "
            f"{pre_correlation:.6f} to {post_correlation:.6f}."
        )
    if component.family in {
        StressFamily.ONE_DAY_GAP,
        StressFamily.SUSTAINED_CUMULATIVE_SHOCK,
    }:
        return " ".join(
            (
                f"{symbol.value} cumulative return changed from "
                f"{before_assets[symbol].cumulative_simple_return:+.6%} to "
                f"{after_assets[symbol].cumulative_simple_return:+.6%}."
            )
            for symbol in (Symbol.SPY, Symbol.TLT)
        )
    if component.family is StressFamily.TRANSACTION_COST_MULTIPLIER:
        return (
            "Transaction costs changed from "
            f"{summary.transaction_cost_bps_before:.6f} to "
            f"{summary.transaction_cost_bps_after:.6f} basis points."
        )
    raise DefenderServiceError("verified replay contains an unsupported stress family")


def render_defender_markdown(
    report: FailureReport,
    replay_records: Sequence[ScenarioEvaluationRecord],
) -> str:
    """Render Markdown using only defender-verified structured numeric fields."""
    replay_by_id = {record.result.scenario_id: record for record in replay_records}
    verdict_by_id = {verdict.scenario_id: verdict for verdict in report.defender_verdicts}
    lines = [
        "# Defender failure report",
        "",
        "> **Research only; not investment advice.**",
        "",
        "## Verified provenance",
        "",
        f"- Experiment: `{html.escape(report.experiment_id)}`",
        f"- Dataset SHA-256: `{report.data_sha256}`",
        f"- Configuration SHA-256: `{report.config_sha256}`",
        f"- Code version: `{html.escape(report.code_version)}`",
        f"- Seed: {report.seed}",
        "",
        "## Independently replayed baseline",
        "",
    ]
    if report.baseline_metrics is None:
        lines.extend(
            [
                "Baseline metrics are unavailable because immutable provenance did not verify.",
                "",
            ]
        )
    else:
        baseline = report.baseline_metrics
        lines.extend(
            [
                f"- Total return: {baseline.total_return:+.6%}",
                f"- Maximum drawdown: {baseline.maximum_drawdown:.6%}",
                (
                    "- Worst rolling twenty-row return: "
                    f"{baseline.worst_rolling_20_day_return:+.6%}"
                ),
                f"- Annualized volatility: {baseline.annualized_volatility:.6%}",
                f"- Earned return rows: {baseline.observation_count}",
                "",
            ]
        )
    lines.extend(["## Verified failures", ""])
    if not report.verified_results:
        lines.extend(
            [
                "No attacker failure was independently reproduced.",
                "",
            ]
        )
    for result in report.verified_results:
        record = replay_by_id[result.scenario_id]
        scenario = record.scenario
        if scenario is None or result.metrics is None:
            raise DefenderServiceError("verified report result lacks replay evidence")
        lines.extend(
            [
                f"### Scenario `{html.escape(result.scenario_id)}`",
                "",
                "**Verified causal boundary:** "
                + html.escape(report.scenario_explanations[result.scenario_id]),
                "",
                f"**Explicit numeric stress:** {_stress_description(scenario)}.",
                "",
                (
                    "**Replayed portfolio metrics:** total return "
                    f"{result.metrics.total_return:+.6%}; maximum drawdown "
                    f"{result.metrics.maximum_drawdown:.6%}; worst rolling twenty-row "
                    f"return {result.metrics.worst_rolling_20_day_return:+.6%}; annualized "
                    f"volatility {result.metrics.annualized_volatility:.6%}."
                ),
                "",
                "**Breached rules, timing, and propagation:**",
                "",
            ]
        )
        for breach, window in zip(result.breaches, record.worst_windows, strict=True):
            contributions = ", ".join(
                f"{symbol.value} {value:+.6%}"
                for symbol, value in window.asset_return_contributions.items()
            )
            weights = ", ".join(
                f"{symbol.value} {value:.6%}"
                for symbol, value in window.average_effective_weights.items()
            )
            lines.extend(
                [
                    (
                        f"- `{html.escape(breach.rule_id)}` began on {breach.onset_date}; "
                        f"observed {breach.observed_value:.6f} versus threshold "
                        f"{breach.threshold:.6f}, normalized excess "
                        f"{breach.normalized_excess:.6f}. Worst window: "
                        f"{breach.worst_window_start} through {breach.worst_window_end}."
                    ),
                    (
                        f"  Linked contributions: {contributions}; transaction costs "
                        f"{window.transaction_cost_return_contribution:+.6%}; portfolio "
                        f"compounded return {window.portfolio_compounded_return:+.6%}; "
                        f"average effective positions {weights}."
                    ),
                ]
            )
        lines.extend(
            [
                "",
                "**Defence:** Reproduced from the immutable dataset with matching hashes, "
                "code/config version, event dates, and metrics within the configured "
                "absolute tolerance "
                f"{verdict_by_id[result.scenario_id].comparison_tolerance:.12g}.",
                "",
            ]
        )
    rejected = tuple(
        verdict
        for verdict in report.defender_verdicts
        if verdict.verdict is not DefenderVerdictValue.REPRODUCED
    )
    lines.extend(["## Contradicted or invalid evidence", ""])
    if not rejected:
        lines.extend(["None.", ""])
    for verdict in rejected:
        lines.append(
            f"- `{html.escape(verdict.scenario_id)}`: `{verdict.verdict.value}`. "
            + " ".join(html.escape(reason) for reason in verdict.reasons)
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {html.escape(limitation)}" for limitation in report.limitations)
    lines.extend(["", html.escape(report.summary), ""])
    return "\n".join(lines)


class DefenderService:
    """Reload immutable evidence and independently replay at most TOP_K scenarios."""

    def __init__(
        self,
        *,
        store: LocalDatasetStore | None,
        report_writer: ReportWriter,
        prompt: str | None = None,
        weights_csv: Path | None = None,
    ) -> None:
        self.store = store
        self.report_writer = report_writer
        self.prompt = _load_prompt(DEFENDER_PROMPT_PATH) if prompt is None else prompt
        self.weights_csv = weights_csv

    def defend(
        self,
        *,
        attack_run: AttackRun,
        manifest_path: Path | None = None,
        dataset: StoredDataset | None = None,
        report_path: Path | None = None,
    ) -> DefenseRun:
        if (manifest_path is None) == (dataset is None):
            raise DefenderServiceError(
                "provide exactly one immutable manifest path or verified dataset"
            )
        experiment = attack_run.experiment
        canonical_config_hash = canonical_json_sha256(experiment)
        bounded_records = attack_run.top_failures[:TOP_K]
        global_budget_valid = (
            len(attack_run.top_failures) <= min(experiment.top_k, TOP_K)
            and tuple(record.result.rank for record in bounded_records)
            == tuple(range(1, len(bounded_records) + 1))
        )

        verified_dataset: StoredDataset | None = None
        baseline: BacktestResult | None = None
        strategy: Strategy | None = None
        dataset_verified = False
        try:
            if dataset is not None:
                loaded = dataset
            else:
                if self.store is None or manifest_path is None:
                    raise DatasetVerificationError("local dataset store is unavailable")
                loaded = self.store.validate(manifest_path)
            loaded_manifest_hash = hashlib.sha256(
                canonical_manifest_bytes(loaded.manifest)
            ).hexdigest()
            dataset_verified = (
                loaded.manifest.dataset_id == experiment.dataset_id
                and loaded.manifest.sha256 == experiment.data_sha256
                and loaded.manifest == attack_run.dataset_manifest
                and loaded_manifest_hash == attack_run.dataset_manifest_sha256
            )
            if dataset_verified:
                verified_dataset = loaded
                strategy = strategy_from_spec(
                    experiment.strategy,
                    experiment.numeric_tolerance,
                    self.weights_csv,
                )
                baseline = run_backtest(
                    verified_dataset,
                    strategy,
                    experiment.transaction_cost_bps,
                    experiment.numeric_tolerance,
                )
        except (DatasetVerificationError, StrategyError, BacktestError):
            dataset_verified = False

        verdicts: list[DefenderVerdict] = []
        replay_records: list[ScenarioEvaluationRecord] = []
        for position, expected in enumerate(bounded_records, start=1):
            scenario = expected.scenario
            schema_valid = scenario is not None
            data_hash_matches = dataset_verified and (
                expected.result.dataset_id == experiment.dataset_id
                and expected.result.data_sha256 == experiment.data_sha256
            )
            config_hash_matches = (
                attack_run.config_sha256 == canonical_config_hash
                and expected.result.config_sha256 == canonical_config_hash
            )
            code_version_matches = expected.result.code_version == experiment.code_version
            scenario_identity_matches = scenario is not None and (
                expected.result.scenario_id == scenario.scenario_id
                and expected.result.input_sha256 == canonical_json_sha256(scenario)
            )
            budget_valid = global_budget_valid and expected.result.rank == position
            checks = (
                schema_valid,
                data_hash_matches,
                config_hash_matches,
                code_version_matches,
                scenario_identity_matches,
                budget_valid,
            )
            if (
                not all(checks)
                or verified_dataset is None
                or strategy is None
                or baseline is None
            ):
                reasons: list[str] = []
                if not schema_valid:
                    reasons.append("Scenario evidence was not schema-valid.")
                if not data_hash_matches:
                    reasons.append("Dataset or immutable manifest hash did not match.")
                if not config_hash_matches:
                    reasons.append("Experiment configuration hash did not match.")
                if not code_version_matches:
                    reasons.append("Code version did not match.")
                if not scenario_identity_matches:
                    reasons.append("Canonical scenario identity did not match.")
                if not budget_valid:
                    reasons.append("TOP_K ordering or attack budget was invalid.")
                verdicts.append(
                    DefenderVerdict(
                        scenario_id=expected.result.scenario_id,
                        verdict=DefenderVerdictValue.INVALID_EVIDENCE,
                        schema_valid=schema_valid,
                        data_hash_matches=data_hash_matches,
                        config_hash_matches=config_hash_matches,
                        code_version_matches=code_version_matches,
                        scenario_identity_matches=scenario_identity_matches,
                        budget_valid=budget_valid,
                        result_matches=False,
                        event_dates_match=False,
                        transform_hash_matches=False,
                        replay_metrics=None,
                        max_metric_delta=None,
                        comparison_tolerance=experiment.numeric_tolerance,
                        reasons=tuple(reasons),
                    )
                )
                continue

            assert scenario is not None
            replayed = evaluate_scenario(
                dataset=verified_dataset,
                strategy=strategy,
                experiment=experiment,
                baseline=baseline,
                scenario=scenario,
                round_number=expected.round_number,
                candidate_number=expected.candidate_number,
                config_sha256=canonical_config_hash,
            )
            replay_records.append(replayed)
            result_matches, dates_match, transform_matches, max_delta = (
                _record_replay_matches(
                    expected,
                    replayed,
                    experiment.numeric_tolerance,
                )
            )
            replay_metrics = replayed.result.metrics
            reproduced = (
                replayed.result.status.value == "valid"
                and result_matches
                and dates_match
                and transform_matches
                and max_delta is not None
                and max_delta <= experiment.numeric_tolerance
            )
            replay_reasons = (
                () if reproduced else ("Deterministic replay did not match evidence.",)
            )
            verdicts.append(
                DefenderVerdict(
                    scenario_id=expected.result.scenario_id,
                    verdict=(
                        DefenderVerdictValue.REPRODUCED
                        if reproduced
                        else DefenderVerdictValue.NOT_REPRODUCED
                    ),
                    schema_valid=True,
                    data_hash_matches=True,
                    config_hash_matches=True,
                    code_version_matches=True,
                    scenario_identity_matches=True,
                    budget_valid=True,
                    result_matches=result_matches,
                    event_dates_match=dates_match,
                    transform_hash_matches=transform_matches,
                    replay_metrics=replay_metrics,
                    max_metric_delta=max_delta,
                    comparison_tolerance=experiment.numeric_tolerance,
                    reasons=replay_reasons,
                )
            )

        item_records = {record.result.scenario_id: record for record in bounded_records}
        writer_items: list[DefenderEvidenceItem] = []
        for verdict in verdicts:
            source_record = item_records[verdict.scenario_id]
            source_scenario = source_record.scenario
            if source_scenario is None:
                continue
            writer_items.append(
                DefenderEvidenceItem(
                    scenario_id=verdict.scenario_id,
                    replay_verdict=verdict.verdict,
                    scenario_families=_scenario_families(source_scenario),
                    breached_rule_ids=tuple(
                        breach.rule_id for breach in source_record.result.breaches
                    ),
                    attacker_hypothesis_untrusted=source_scenario.hypothesis,
                    attacker_headline_untrusted=source_scenario.headline,
                )
            )
        writer_evidence = DefenderEvidenceSummary(
            experiment_id=experiment.experiment_id,
            items=tuple(writer_items),
        )
        accepted, narrative_rejections = _audit_narrative(
            writer=self.report_writer,
            prompt=self.prompt,
            evidence=writer_evidence,
        )
        accepted_by_id = {assessment.scenario_id: assessment for assessment in accepted}
        verdict_by_id = {verdict.scenario_id: verdict for verdict in verdicts}
        verified_records: list[ScenarioEvaluationRecord] = []
        for replayed in replay_records:
            verdict = verdict_by_id[replayed.result.scenario_id]
            if verdict.verdict is DefenderVerdictValue.REPRODUCED:
                expected_rank = item_records[replayed.result.scenario_id].result.rank
                result_payload = replayed.result.model_dump(mode="python")
                result_payload["rank"] = expected_rank
                verified_records.append(
                    replayed.model_copy(
                        update={"result": StressResult.model_validate(result_payload)}
                    )
                )
        explanations = {
            record.result.scenario_id: _fixed_explanation(record, accepted_by_id)
            for record in verified_records
        }
        report = FailureReport(
            notice="Research only; not investment advice.",
            experiment_id=experiment.experiment_id,
            data_sha256=experiment.data_sha256,
            config_sha256=canonical_config_hash,
            code_version=experiment.code_version,
            seed=experiment.seed,
            baseline_metrics=(
                baseline.metrics if dataset_verified and baseline is not None else None
            ),
            verified_results=tuple(record.result for record in verified_records),
            defender_verdicts=tuple(verdicts),
            scenario_explanations=explanations,
            limitations=(
                "Daily adjusted prices cover only SPY and TLT and omit intraday behavior.",
                "Deterministic replay verifies implementation evidence, not scenario "
                "likelihood.",
                "Execution is frictionless except for the configured transaction-cost assumption.",
                "Attacker headlines and hypotheses are untrusted metadata and support no numbers.",
            ),
            summary=(
                f"Deterministic defence reproduced {len(verified_records)} of "
                f"{len(verdicts)} bounded attacker failures."
            ),
        )
        markdown = render_defender_markdown(report, verified_records)
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with report_path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(markdown)
            except OSError as error:
                raise DefenderServiceError("could not create trusted report path") from error
        return DefenseRun(
            verdicts=tuple(verdicts),
            report=report,
            markdown=markdown,
            replay_records=tuple(replay_records),
            accepted_assessments=accepted,
            narrative_rejections=narrative_rejections,
        )
