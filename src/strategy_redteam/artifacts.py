"""Atomic Gate 6 run bundles, integrity verification, and template reporting."""

from __future__ import annotations

import hashlib
import html
import os
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Self, TypeVar

from pydantic import Field, StrictBool, TypeAdapter, model_validator

from strategy_redteam.attack import (
    AttackPolicy,
    AttackRun,
    ProposalRecord,
    ScenarioEvaluationRecord,
    StopReason,
    canonical_json_bytes,
    canonical_json_sha256,
)
from strategy_redteam.data import canonical_manifest_bytes
from strategy_redteam.domain import (
    MAX_CANDIDATES_PER_ROUND,
    MAX_ROUNDS,
    MAX_TOTAL_SCENARIOS,
    TOP_K,
    ContractModel,
    DataManifest,
    ExperimentSpec,
    MetricSet,
    NonNegativeInt,
    ResultStatus,
    SchemaVersion,
    Sha256,
    StressComponent,
    StressFamily,
    Symbol,
)

REQUIRED_ARTIFACT_FILES = frozenset(
    {
        "experiment.json",
        "dataset_manifest.json",
        "policy.json",
        "proposed_scenarios.jsonl",
        "results.jsonl",
        "top_failures.json",
        "failure_report.md",
    }
)
HASHED_ARTIFACT_FILES = REQUIRED_ARTIFACT_FILES - {"experiment.json"}
ArtifactRecord = TypeVar("ArtifactRecord", bound=ContractModel)
RANKING_METHOD = (
    "breach_count descending",
    "maximum_normalized_excess descending",
    "total_normalized_excess descending",
    "worst_portfolio_loss descending",
    "scenario_id ascending",
)


class ArtifactError(Exception):
    """Base class for artifact publication and verification failures."""


class ArtifactWriteError(ArtifactError):
    """A complete hidden bundle could not be published atomically."""


class ArtifactIntegrityError(ArtifactError):
    """A run bundle is incomplete, altered, or internally inconsistent."""


class ArtifactReference(ContractModel):
    """Hash and exact byte length for one non-index artifact."""

    schema_version: SchemaVersion = "1.0"
    sha256: Sha256
    byte_length: NonNegativeInt


class BudgetArtifact(ContractModel):
    """Recorded configured values alongside immutable repository hard limits."""

    schema_version: SchemaVersion = "1.0"
    max_rounds: Annotated[int, Field(strict=True, ge=1, le=MAX_ROUNDS)]
    max_candidates_per_round: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_CANDIDATES_PER_ROUND),
    ]
    max_total_scenarios: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_TOTAL_SCENARIOS),
    ]
    top_k: Annotated[int, Field(strict=True, ge=1, le=TOP_K)]
    hard_max_rounds: Literal[3] = 3
    hard_max_candidates_per_round: Literal[8] = 8
    hard_max_total_scenarios: Literal[24] = 24
    hard_top_k: Literal[3] = 3


class ExperimentArtifact(ContractModel):
    """Run index written last inside the hidden bundle before publication."""

    schema_version: SchemaVersion = "1.0"
    runner_version: Literal["attack-runner-1.0"] = "attack-runner-1.0"
    experiment: ExperimentSpec
    config_sha256: Sha256
    dataset_manifest_sha256: Sha256
    policy_id: str
    policy_sha256: Sha256
    baseline_metrics: MetricSet
    budget: BudgetArtifact
    rounds_started: Annotated[int, Field(strict=True, ge=0, le=MAX_ROUNDS)]
    candidate_slots_consumed: Annotated[
        int,
        Field(strict=True, ge=0, le=MAX_TOTAL_SCENARIOS),
    ]
    evaluated_scenarios: Annotated[
        int,
        Field(strict=True, ge=0, le=MAX_TOTAL_SCENARIOS),
    ]
    rejected_scenarios: Annotated[
        int,
        Field(strict=True, ge=0, le=MAX_TOTAL_SCENARIOS),
    ]
    top_failure_count: Annotated[int, Field(strict=True, ge=0, le=TOP_K)]
    stop_reason: StopReason
    evidence_condition_met: StrictBool
    attack_completed: StrictBool
    artifact_bundle_complete: Literal[True] = True
    artifact_files: dict[str, ArtifactReference]

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if set(self.artifact_files) != HASHED_ARTIFACT_FILES:
            raise ValueError("artifact index must contain every non-index artifact exactly once")
        if self.evaluated_scenarios + self.rejected_scenarios != self.candidate_slots_consumed:
            raise ValueError("evaluated and rejected counts must consume every candidate slot")
        interrupted = self.stop_reason in {
            StopReason.TIMEOUT,
            StopReason.PROPOSER_BUDGET_VIOLATION,
        }
        if self.attack_completed == interrupted:
            raise ValueError("attack_completed contradicts the stop reason")
        return self


class TopFailuresArtifact(ContractModel):
    """Bounded selected failures with an explicit non-composite ranking method."""

    schema_version: SchemaVersion = "1.0"
    ranking_method: tuple[str, ...]
    top_k: Annotated[int, Field(strict=True, ge=1, le=TOP_K)]
    failures: tuple[ScenarioEvaluationRecord, ...] = Field(default=(), max_length=TOP_K)

    @model_validator(mode="after")
    def validate_ranked_failures(self) -> Self:
        if self.ranking_method != RANKING_METHOD:
            raise ValueError("ranking method does not match the documented severity tuple")
        expected_ranks = tuple(range(1, len(self.failures) + 1))
        actual_ranks = tuple(record.result.rank for record in self.failures)
        if actual_ranks != expected_ranks:
            raise ValueError("top failures must carry contiguous bounded ranks")
        if any(
            record.result.status is not ResultStatus.VALID
            or record.result.breach_count == 0
            for record in self.failures
        ):
            raise ValueError("top failures must contain breached valid evidence")
        return self


def _artifact_reference(content: bytes) -> ArtifactReference:
    return ArtifactReference(
        sha256=hashlib.sha256(content).hexdigest(),
        byte_length=len(content),
    )


def _json_lines(records: tuple[ContractModel, ...]) -> bytes:
    return b"".join(canonical_json_bytes(record) for record in records)


def _build_experiment_artifact(
    run: AttackRun,
    references: dict[str, ArtifactReference],
) -> ExperimentArtifact:
    experiment = run.experiment
    return ExperimentArtifact(
        experiment=experiment,
        config_sha256=run.config_sha256,
        dataset_manifest_sha256=run.dataset_manifest_sha256,
        policy_id=run.policy.policy_id,
        policy_sha256=run.policy_sha256,
        baseline_metrics=run.baseline_metrics,
        budget=BudgetArtifact(
            max_rounds=experiment.max_rounds,
            max_candidates_per_round=experiment.max_candidates_per_round,
            max_total_scenarios=experiment.max_total_scenarios,
            top_k=experiment.top_k,
        ),
        rounds_started=run.rounds_started,
        candidate_slots_consumed=run.candidate_slots_consumed,
        evaluated_scenarios=run.evaluated_scenarios,
        rejected_scenarios=run.rejected_scenarios,
        top_failure_count=len(run.top_failures),
        stop_reason=run.stop_reason,
        evidence_condition_met=run.evidence_condition_met,
        attack_completed=run.attack_completed,
        artifact_files=references,
    )


def _stress_description(component: StressComponent) -> str:
    if component.family is StressFamily.ONE_DAY_GAP:
        shocks = ", ".join(
            f"{symbol.value} {value:+.6%}" for symbol, value in (component.shocks or {}).items()
        )
        return f"one-day incremental shock on {component.date}: {shocks}"
    if component.family is StressFamily.SUSTAINED_CUMULATIVE_SHOCK:
        shocks = ", ".join(
            f"{symbol.value} {value:+.6%}" for symbol, value in (component.shocks or {}).items()
        )
        return (
            f"cumulative shock from {component.start_date} over "
            f"{component.duration_rows} observed rows: {shocks}"
        )
    if component.family is StressFamily.VOLATILITY_MULTIPLIER:
        symbols = ", ".join(symbol.value for symbol in (component.symbols or ()))
        return (
            f"{component.volatility_multiplier:.6f}x log-return volatility for {symbols} "
            f"from {component.start_date} through {component.end_date}"
        )
    if component.family is StressFamily.CORRELATION_TARGET:
        return (
            f"SPY/TLT correlation target {component.target_correlation:.6f} "
            f"from {component.start_date} through {component.end_date}"
        )
    if component.family is StressFamily.TRANSACTION_COST_MULTIPLIER:
        return f"{component.transaction_cost_multiplier:.6f}x transaction costs"
    return "unsupported historical component"


def _mechanism(families: tuple[StressFamily, ...]) -> str:
    labels = {
        StressFamily.ONE_DAY_GAP: "resilience to abrupt daily gaps",
        StressFamily.SUSTAINED_CUMULATIVE_SHOCK: "resilience to persistent cumulative moves",
        StressFamily.VOLATILITY_MULTIPLIER: "stability under higher return dispersion",
        StressFamily.CORRELATION_TARGET: "the SPY/TLT diversification relationship",
        StressFamily.TRANSACTION_COST_MULTIPLIER: "the assumed execution-cost regime",
    }
    assumptions = tuple(labels[family] for family in families)
    return "The scenario challenged " + "; ".join(assumptions) + "."


def _asset_response(record: ScenarioEvaluationRecord) -> str:
    before = record.pre_transform_summary
    after = record.post_transform_summary
    if before is None or after is None:
        return "No transform response is available."
    before_by_symbol = {asset.symbol: asset for asset in before.assets}
    after_by_symbol = {asset.symbol: asset for asset in after.assets}
    return "; ".join(
        (
            f"{symbol.value} evaluation-window return changed from "
            f"{before_by_symbol[symbol].cumulative_simple_return:+.6%} to "
            f"{after_by_symbol[symbol].cumulative_simple_return:+.6%}"
        )
        for symbol in (Symbol.SPY, Symbol.TLT)
    )


def render_failure_report(run: AttackRun) -> str:
    """Render only validated numeric evidence and fixed, non-agent prose."""
    lines = [
        "# Failure report",
        "",
        "> **Research only; not investment advice.** This bounded stress test does not "
        "estimate likelihood, forecast returns, or recommend a portfolio.",
        "",
        "## Run summary",
        "",
        f"- Experiment: `{html.escape(run.experiment.experiment_id)}`",
        f"- Dataset SHA-256: `{run.experiment.data_sha256}`",
        f"- Stop reason: `{run.stop_reason.value}`",
        f"- Rounds started: {run.rounds_started} of {run.experiment.max_rounds}",
        (
            f"- Candidate slots consumed: {run.candidate_slots_consumed} of "
            f"{run.experiment.max_total_scenarios}"
        ),
        f"- Valid evaluations: {run.evaluated_scenarios}",
        f"- Typed rejections: {run.rejected_scenarios}",
        "",
        "## Ranking method",
        "",
        (
            "Failures are ordered by the documented internal severity tuple: breach count, "
            "maximum normalized excess, total normalized excess, worst portfolio-loss "
            "magnitude, then scenario ID. No composite or magic score is calculated or "
            "presented."
        ),
        "",
        "## Failure evidence",
        "",
    ]
    if not run.top_failures:
        lines.extend(
            [
                (
                    "No configured failure was found among the valid evaluated scenarios. "
                    "This honest negative result does not establish robustness outside the "
                    "tested policy, data, thresholds, or budget."
                ),
                "",
                "**Defender status:** Not run in Gate 6; no result has been independently "
                "reproduced.",
                "",
            ]
        )
    for record in run.top_failures:
        scenario = record.scenario
        result = record.result
        if scenario is None:
            raise ArtifactWriteError("ranked valid result has no typed scenario")
        lines.extend(
            [
                f"### {result.rank}. Scenario `{html.escape(result.scenario_id)}`",
                "",
                f"**Failure mechanism:** {_mechanism(result_family_tuple(scenario))}",
                "",
                "**Explicit numeric stress:** "
                + "; then ".join(_stress_description(item) for item in scenario.components)
                + ".",
                "",
                f"**Market-to-portfolio propagation:** {_asset_response(record)}.",
                "",
                "**Breached rules and timing:**",
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
                        f"- `{breach.rule_id}` ({breach.family.value}) onset: "
                        f"{breach.onset_date}; observed {breach.observed_value:.6f} against "
                        f"threshold {breach.threshold:.6f} (normalized excess "
                        f"{breach.normalized_excess:.6f}). Worst window: "
                        f"{breach.worst_window_start} through {breach.worst_window_end}."
                    ),
                    (
                        f"  Linked contributions over that worst window: {contributions}; "
                        f"transaction costs {window.transaction_cost_return_contribution:+.6%}; "
                        f"portfolio compounded return {window.portfolio_compounded_return:+.6%}. "
                        f"Average effective positions: {weights}."
                    ),
                ]
            )
        lines.extend(
            [
                "",
                "**Defender status:** Not run in Gate 6. This is deterministic attacker "
                "evidence, not independently reproduced evidence.",
                "",
            ]
        )
    lines.extend(
        [
            "## Limitations",
            "",
            "- The immutable daily adjusted-price dataset covers only SPY and TLT; it does not "
            "represent intraday gaps, market impact, liquidity, taxes, or future conditions.",
            "- Strategy decisions stamped at close t first affect the return earned on the next "
            "observed row; execution assumptions are simplified and explicit.",
            "- Numeric stresses are adversarial tests, not claims about scenario probability or "
            "forecasts of market behavior.",
            "- Search is limited by the versioned policy, deterministic proposer, configured "
            "thresholds, three rounds, eight candidates per round, 24 total scenarios, and the "
            "wall-clock deadline.",
            "- Gate 6 does not perform independent defender replay; later verification must use "
            "the same dataset, configuration, code version, and canonical scenario evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def result_family_tuple(scenario: object) -> tuple[StressFamily, ...]:
    """Return ordered unique families without inspecting any narrative text."""
    if not hasattr(scenario, "components"):
        raise ArtifactWriteError("scenario does not expose typed components")
    components = scenario.components
    return tuple(dict.fromkeys(component.family for component in components))


def _write_new_file(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def write_run_artifacts(directory: Path, run: AttackRun) -> ExperimentArtifact:
    """Publish the seven-file run bundle only after hidden staging verifies."""
    directory = directory.resolve()
    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        raise ArtifactWriteError(f"artifact directory already exists: {directory}")

    top_failures = TopFailuresArtifact(
        ranking_method=RANKING_METHOD,
        top_k=run.experiment.top_k,
        failures=run.top_failures,
    )
    non_index_content = {
        "dataset_manifest.json": canonical_manifest_bytes(run.dataset_manifest),
        "policy.json": canonical_json_bytes(run.policy),
        "proposed_scenarios.jsonl": _json_lines(run.proposals),
        "results.jsonl": _json_lines(run.evaluations),
        "top_failures.json": canonical_json_bytes(top_failures),
        "failure_report.md": render_failure_report(run).encode("utf-8"),
    }
    references = {
        name: _artifact_reference(content) for name, content in non_index_content.items()
    }
    experiment_artifact = _build_experiment_artifact(run, references)
    all_content = {
        "experiment.json": canonical_json_bytes(experiment_artifact),
        **non_index_content,
    }
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{directory.name}.tmp-", dir=str(parent))
    ).resolve()
    try:
        for name in sorted(REQUIRED_ARTIFACT_FILES):
            _write_new_file(temporary / name, all_content[name])
        verify_run_artifacts(temporary)
        os.replace(temporary, directory)
    except Exception as error:
        if temporary.exists():
            shutil.rmtree(temporary)
        if isinstance(error, ArtifactError):
            raise
        raise ArtifactWriteError(f"atomic artifact publication failed: {error}") from error
    return experiment_artifact


def _read_json_lines(
    path: Path,
    adapter: TypeAdapter[ArtifactRecord],
) -> tuple[ArtifactRecord, ...]:
    try:
        lines = path.read_bytes().splitlines()
        return tuple(adapter.validate_json(line) for line in lines)
    except (OSError, ValueError) as error:
        raise ArtifactIntegrityError(f"invalid JSONL artifact {path.name}: {error}") from error


def verify_run_artifacts(directory: Path) -> ExperimentArtifact:
    """Verify exact membership, hashes, typed JSON, counts, and provenance links."""
    try:
        names = {path.name for path in directory.iterdir() if path.is_file()}
    except OSError as error:
        raise ArtifactIntegrityError(f"cannot inspect artifact directory: {error}") from error
    if names != REQUIRED_ARTIFACT_FILES:
        raise ArtifactIntegrityError("run bundle does not contain exactly the required artifacts")
    try:
        experiment = ExperimentArtifact.model_validate_json(
            (directory / "experiment.json").read_bytes()
        )
    except (OSError, ValueError) as error:
        raise ArtifactIntegrityError(f"invalid experiment.json: {error}") from error
    for name, reference in experiment.artifact_files.items():
        try:
            content = (directory / name).read_bytes()
        except OSError as error:
            raise ArtifactIntegrityError(f"cannot read indexed artifact {name}: {error}") from error
        if len(content) != reference.byte_length:
            raise ArtifactIntegrityError(f"artifact byte length mismatch: {name}")
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise ArtifactIntegrityError(f"artifact hash mismatch: {name}")
    try:
        manifest = DataManifest.model_validate_json(
            (directory / "dataset_manifest.json").read_bytes()
        )
        policy = AttackPolicy.model_validate_json((directory / "policy.json").read_bytes())
        top = TopFailuresArtifact.model_validate_json(
            (directory / "top_failures.json").read_bytes()
        )
    except ValueError as error:
        raise ArtifactIntegrityError(f"typed artifact validation failed: {error}") from error
    if hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest() != (
        experiment.dataset_manifest_sha256
    ):
        raise ArtifactIntegrityError("dataset manifest provenance hash mismatch")
    if canonical_json_sha256(policy) != experiment.policy_sha256:
        raise ArtifactIntegrityError("policy provenance hash mismatch")
    if policy.policy_id != experiment.policy_id:
        raise ArtifactIntegrityError("policy ID does not match experiment summary")
    if canonical_json_sha256(experiment.experiment) != experiment.config_sha256:
        raise ArtifactIntegrityError("experiment configuration hash mismatch")
    if (
        manifest.dataset_id != experiment.experiment.dataset_id
        or manifest.sha256 != experiment.experiment.data_sha256
    ):
        raise ArtifactIntegrityError("dataset manifest does not match experiment inputs")
    proposals = _read_json_lines(
        directory / "proposed_scenarios.jsonl",
        TypeAdapter(ProposalRecord),
    )
    evaluations = _read_json_lines(
        directory / "results.jsonl",
        TypeAdapter(ScenarioEvaluationRecord),
    )
    if len(proposals) != experiment.candidate_slots_consumed:
        raise ArtifactIntegrityError("proposal count does not match consumed candidate slots")
    if len(evaluations) != experiment.candidate_slots_consumed:
        raise ArtifactIntegrityError("result count does not match consumed candidate slots")
    for proposal, evaluation in zip(proposals, evaluations, strict=True):
        if (
            proposal.round_number != evaluation.round_number
            or proposal.candidate_number != evaluation.candidate_number
            or proposal.scenario_id != evaluation.result.scenario_id
            or proposal.input_sha256 != evaluation.result.input_sha256
        ):
            raise ArtifactIntegrityError("proposal and result records do not align")
    valid_count = sum(
        record.result.status is ResultStatus.VALID
        for record in evaluations
    )
    if valid_count != experiment.evaluated_scenarios:
        raise ArtifactIntegrityError("valid result count does not match experiment summary")
    if len(evaluations) - valid_count != experiment.rejected_scenarios:
        raise ArtifactIntegrityError("rejected result count does not match experiment summary")
    if len(top.failures) != experiment.top_failure_count:
        raise ArtifactIntegrityError("top-failure count does not match experiment summary")
    expected_top = sorted(
        (
            record
            for record in evaluations
            if record.result.status is ResultStatus.VALID
            and record.result.breach_count > 0
        ),
        key=lambda record: (
            -record.result.breach_count,
            -record.result.maximum_normalized_excess,
            -record.result.total_normalized_excess,
            -record.result.worst_portfolio_loss,
            record.result.scenario_id,
        ),
    )[: experiment.experiment.top_k]
    expected_locations = tuple(
        (record.round_number, record.candidate_number) for record in expected_top
    )
    actual_locations = tuple(
        (record.round_number, record.candidate_number) for record in top.failures
    )
    if actual_locations != expected_locations:
        raise ArtifactIntegrityError("top failures do not match bounded severity ranking")
    for expected, actual in zip(expected_top, top.failures, strict=True):
        expected_payload = expected.model_dump(mode="json")
        actual_payload = actual.model_dump(mode="json")
        actual_result = actual_payload["result"]
        if not isinstance(actual_result, dict):
            raise ArtifactIntegrityError("top failure result has an invalid shape")
        actual_result["rank"] = None
        if canonical_json_bytes(expected_payload) != canonical_json_bytes(actual_payload):
            raise ArtifactIntegrityError("top failure evidence differs from results.jsonl")
    report = (directory / "failure_report.md").read_text(encoding="utf-8")
    if not report.startswith("# Failure report\n\n> **Research only; not investment advice.**"):
        raise ArtifactIntegrityError("failure report is missing the required opening notice")
    return experiment
