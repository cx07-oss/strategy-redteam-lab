"""Complete local/offline orchestration and verified artifact publication.

The offline clients are deterministic schema adapters. They can propose only typed
numeric stresses and causal labels; the engine remains the sole source of market
metrics, dates, contributions, and replay comparisons.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml  # type: ignore[import-untyped]
from pydantic import Field, TypeAdapter, ValidationError, model_validator

from strategy_redteam.artifacts import (
    ArtifactIntegrityError,
    ArtifactReference,
    BudgetArtifact,
    ExperimentArtifact,
    TopFailuresArtifact,
    verify_run_artifacts,
)
from strategy_redteam.attack import (
    AttackCatalog,
    AttackError,
    AttackHypothesisPolicy,
    AttackPolicy,
    AttackRun,
    InflationCorrelationHypothesisPolicy,
    RebalanceTimingHypothesisPolicy,
    ScenarioEvaluationRecord,
    TradingFrictionHypothesisPolicy,
    VolatilityRegimeHypothesisPolicy,
    canonical_json_bytes,
)
from strategy_redteam.data import (
    DatasetVerificationError,
    LocalDatasetStore,
    StoredDataset,
    canonical_manifest_bytes,
)
from strategy_redteam.domain import (
    HISTORICAL_WINDOW_ROWS,
    MAX_CANDIDATES_PER_ROUND,
    MAX_ROUNDS,
    MAX_TOTAL_SCENARIOS,
    TOP_K,
    AttackBatch,
    ContractModel,
    DataManifest,
    DefenderVerdict,
    DefenderVerdictValue,
    ExperimentSpec,
    FailureReport,
    FailureRule,
    SchemaVersion,
    StrategySpec,
    StressComponent,
    StressFamily,
    StressResult,
    StressScenario,
    Symbol,
)
from strategy_redteam.model_provider import (
    ModelProviderConfiguration,
    ModelProviderConfigurationError,
    build_report_writer,
    build_scenario_proposer,
)
from strategy_redteam.services import (
    AttackerEvidenceSummary,
    AttackerService,
    CausalClaimAssessment,
    CausalClaimStatus,
    DefenderEvidenceSummary,
    DefenderNarrativeBatch,
    DefenderService,
    DefenseRun,
)
from strategy_redteam.strategy import StrategyError, strategy_from_spec
from strategy_redteam.telemetry import RunTelemetry, build_run_telemetry

MAX_OFFLINE_CONFIG_BYTES = 65_536
OFFLINE_RUNNER_VERSION = "offline-runner-1.0"
OFFLINE_ATTACK_DIRECTORY = "attack"
OFFLINE_ROOT_ARTIFACT_FILES = frozenset(
    {
        "defender_verdicts.json",
        "failure_report.json",
        "failure_report.md",
        "offline_run.json",
        "replay_results.jsonl",
        "telemetry.json",
    }
)
OFFLINE_ATTACK_ARTIFACT_FILES = frozenset(
    {
        f"{OFFLINE_ATTACK_DIRECTORY}/{name}"
        for name in {
            "experiment.json",
            "dataset_manifest.json",
            "policy.json",
            "proposed_scenarios.jsonl",
            "results.jsonl",
            "top_failures.json",
            "failure_report.md",
        }
    }
)
OFFLINE_REQUIRED_ARTIFACT_FILES = OFFLINE_ROOT_ARTIFACT_FILES | OFFLINE_ATTACK_ARTIFACT_FILES
OFFLINE_HASHED_ARTIFACT_FILES = OFFLINE_REQUIRED_ARTIFACT_FILES - {"offline_run.json"}


class OfflineRunError(Exception):
    """Base failure for the complete local/offline flow."""


class OfflineConfigError(OfflineRunError):
    """The offline experiment YAML is not canonical, safe, or schema-valid."""


class OfflineReplayError(OfflineRunError):
    """Independent defender replay did not reproduce all selected evidence."""


class OfflineArtifactError(OfflineRunError):
    """The complete offline artifact bundle could not be published."""


class OfflineArtifactIntegrityError(OfflineArtifactError):
    """The offline bundle is incomplete, altered, or internally inconsistent."""


class OfflineExperimentConfig(ContractModel):
    """Dataset-independent YAML configuration bound after immutable validation."""

    schema_version: SchemaVersion = "1.0"
    experiment_id: Annotated[str, Field(min_length=1, max_length=128)]
    strategy: StrategySpec
    failure_rules: tuple[FailureRule, ...] = Field(min_length=1, max_length=3)
    attack_policy: AttackPolicy
    model_provider: ModelProviderConfiguration = ModelProviderConfiguration()
    seed: Annotated[int, Field(strict=True, ge=0, le=4_294_967_295)]
    timeout_seconds: Annotated[float, Field(strict=True, gt=0.0, allow_inf_nan=False)]
    code_version: Annotated[str, Field(min_length=1, max_length=128)]
    numeric_tolerance: Annotated[
        float,
        Field(strict=True, gt=0.0, lt=1.0, allow_inf_nan=False),
    ]
    transaction_cost_bps: Annotated[
        float,
        Field(strict=True, ge=0.0, lt=10_000.0, allow_inf_nan=False),
    ] = 0.0
    historical_window_rows: tuple[Literal[20], Literal[60], Literal[126]] = HISTORICAL_WINDOW_ROWS
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
    def validate_budget_capacity(self) -> Self:
        """Reject inconsistent configured limits before binding a dataset."""
        capacity = self.max_rounds * self.max_candidates_per_round
        if self.max_total_scenarios > capacity:
            raise ValueError("max_total_scenarios exceeds round capacity")
        if self.top_k > self.max_total_scenarios:
            raise ValueError("top_k exceeds max_total_scenarios")
        return self

    def bind_dataset(self, dataset: StoredDataset) -> ExperimentSpec:
        """Create the provenance-bound engine configuration after data verification."""
        return ExperimentSpec(
            experiment_id=self.experiment_id,
            dataset_id=dataset.manifest.dataset_id,
            data_sha256=dataset.manifest.sha256,
            strategy=self.strategy,
            failure_rules=self.failure_rules,
            seed=self.seed,
            timeout_seconds=self.timeout_seconds,
            code_version=self.code_version,
            numeric_tolerance=self.numeric_tolerance,
            transaction_cost_bps=self.transaction_cost_bps,
            historical_window_rows=self.historical_window_rows,
            max_rounds=self.max_rounds,
            max_candidates_per_round=self.max_candidates_per_round,
            max_total_scenarios=self.max_total_scenarios,
            top_k=self.top_k,
        )


class DefenderArtifact(ContractModel):
    """Typed defender output retained separately from replayed engine records."""

    schema_version: SchemaVersion = "1.0"
    verdicts: tuple[DefenderVerdict, ...] = Field(default=(), max_length=TOP_K)
    accepted_assessments: tuple[CausalClaimAssessment, ...] = Field(
        default=(),
        max_length=TOP_K,
    )
    narrative_rejections: tuple[str, ...] = Field(default=(), max_length=TOP_K)


class OfflineRunArtifact(ContractModel):
    """Final index for the exact complete local/offline artifact bundle."""

    schema_version: SchemaVersion = "1.0"
    runner_version: Literal["offline-runner-1.0"] = "offline-runner-1.0"
    mode: Literal["offline"] = "offline"
    verification_status: Literal["verified"] = "verified"
    experiment: ExperimentSpec
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget: BudgetArtifact
    candidate_slots_consumed: Annotated[
        int,
        Field(strict=True, ge=0, le=MAX_TOTAL_SCENARIOS),
    ]
    top_failure_count: Annotated[int, Field(strict=True, ge=0, le=TOP_K)]
    replay_count: Annotated[int, Field(strict=True, ge=0, le=TOP_K)]
    verified_failure_count: Annotated[int, Field(strict=True, ge=0, le=TOP_K)]
    attack_completed: Literal[True] = True
    artifact_bundle_complete: Literal[True] = True
    artifact_files: dict[str, ArtifactReference]

    @model_validator(mode="after")
    def validate_complete_index(self) -> Self:
        """Require exact membership and one replay for every selected failure."""
        if set(self.artifact_files) != OFFLINE_HASHED_ARTIFACT_FILES:
            raise ValueError("offline index must reference every non-index artifact exactly")
        if not (self.top_failure_count == self.replay_count == self.verified_failure_count):
            raise ValueError("every selected failure must be replayed and verified")
        return self


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    """Safe YAML loader that rejects duplicate keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise OfflineConfigError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_offline_config(path: Path) -> OfflineExperimentConfig:
    """Load a bounded UTF-8 YAML config without resolving tags or duplicate keys."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise OfflineConfigError(f"cannot read offline experiment config: {path}") from error
    if not content or len(content) > MAX_OFFLINE_CONFIG_BYTES:
        raise OfflineConfigError(f"offline config must contain 1..{MAX_OFFLINE_CONFIG_BYTES} bytes")
    try:
        payload = yaml.load(content.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise OfflineConfigError(f"invalid UTF-8 YAML config: {error}") from error
    if not isinstance(payload, dict):
        raise OfflineConfigError("offline config root must be a mapping")
    try:
        return OfflineExperimentConfig.model_validate(payload)
    except ValidationError as error:
        raise OfflineConfigError(f"offline config schema validation failed: {error}") from error


def _interpolate_increasing(minimum: float, maximum: float, fraction: float) -> float:
    return float(minimum + fraction * (maximum - minimum))


def _interpolate_adverse(minimum: float, maximum: float, fraction: float) -> float:
    """Move from the least negative permitted shock toward the most negative."""
    return float(maximum + fraction * (minimum - maximum))


@dataclass(frozen=True)
class DeterministicOfflineScenarioClient:
    """JSON-only, model-free proposer for the complete offline vertical slice."""

    market_dates: tuple[date, ...]
    policy: AttackPolicy
    max_total_scenarios: int

    @classmethod
    def from_dataset(
        cls,
        dataset: StoredDataset,
        policy: AttackPolicy,
        max_total_scenarios: int,
    ) -> DeterministicOfflineScenarioClient:
        dates = tuple(timestamp.date() for timestamp in dataset.data.index)
        if len(dates) < 3:
            raise OfflineConfigError("offline attack requires at least three market dates")
        return cls(dates, policy, max_total_scenarios)

    def propose(
        self,
        *,
        prompt: str,
        evidence_summary: AttackerEvidenceSummary,
        attack_catalog: AttackCatalog | None = None,
    ) -> str:
        """Return a bounded AttackBatch; the fixed prompt is intentionally inert here."""
        del prompt, attack_catalog
        scenarios = tuple(
            self._scenario(
                evidence_summary.round_number,
                candidate_number,
                evidence_summary.max_candidates,
                evidence_summary.policy,
            )
            for candidate_number in range(1, evidence_summary.max_candidates + 1)
        )
        return AttackBatch(
            experiment_id=evidence_summary.experiment_id,
            round_number=evidence_summary.round_number,
            scenarios=scenarios,
        ).model_dump_json()

    def _scenario(
        self,
        round_number: int,
        candidate_number: int,
        batch_size: int,
        runtime_policy: AttackPolicy,
    ) -> StressScenario:
        ordinal = (round_number - 1) * MAX_CANDIDATES_PER_ROUND + candidate_number
        fraction = min(
            1.0,
            (candidate_number + (round_number - 1) * 0.25) / batch_size,
        )
        if runtime_policy.hypotheses:
            rows = runtime_policy.hypotheses
            first_row_slots = max(1, MAX_CANDIDATES_PER_ROUND - len(rows) + 1)
            row = (
                rows[0]
                if candidate_number <= first_row_slots
                else rows[min(candidate_number - first_row_slots, len(rows) - 1)]
            )
            search_fraction = fraction if row is rows[0] else 0.0
            return StressScenario(
                scenario_id=f"offline-r{round_number:02d}-c{candidate_number:02d}",
                evaluation_start=self.market_dates[0],
                evaluation_end=self.market_dates[-1],
                components=self._hypothesis_components(row, ordinal, search_fraction),
                hypothesis=(
                    f"Bounded {row.hypothesis_family.value} candidate; narrative cannot "
                    "alter its typed numeric components."
                ),
                headline=f"Offline {row.hypothesis_family.value} stress candidate",
            )
        components = tuple(
            self._component(family, ordinal, fraction) for family in runtime_policy.allowed_families
        )
        return StressScenario(
            scenario_id=f"offline-r{round_number:02d}-c{candidate_number:02d}",
            evaluation_start=self.market_dates[0],
            evaluation_end=self.market_dates[-1],
            components=components,
            hypothesis=(
                "A deterministic composition tests whether rising volatility, positive "
                "equity-bond correlation, and persistent losses break diversification."
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
            window_start = 1
            maximum_window_end = min(
                len(self.market_dates) - row.shock_duration_rows.minimum - 1,
                window_start + row.correlation_volatility_duration_rows.maximum - 1,
            )
            minimum_window_end = window_start + row.correlation_volatility_duration_rows.minimum - 1
            if maximum_window_end < minimum_window_end:
                raise OfflineConfigError("dataset is too short for inflation search dimensions")
            window_end = min(
                maximum_window_end,
                max(minimum_window_end, (len(self.market_dates) - 1) // 2),
            )
            maximum_duration = min(
                row.shock_duration_rows.maximum,
                len(self.market_dates) - window_end - 1,
            )
            duration = row.shock_duration_rows.minimum + round(
                (maximum_duration - row.shock_duration_rows.minimum) * fraction
            )
            latest_start = len(self.market_dates) - duration
            shock_start = max(window_end + 1, round(latest_start * 0.60))
            return (
                StressComponent(
                    family=StressFamily.VOLATILITY_MULTIPLIER,
                    start_date=self.market_dates[window_start],
                    end_date=self.market_dates[window_end],
                    symbols=(Symbol.SPY, Symbol.TLT),
                    volatility_multiplier=_interpolate_increasing(
                        row.volatility_multiplier.minimum,
                        row.volatility_multiplier.maximum,
                        fraction,
                    ),
                ),
                StressComponent(
                    family=StressFamily.CORRELATION_TARGET,
                    start_date=self.market_dates[window_start],
                    end_date=self.market_dates[window_end],
                    target_correlation=_interpolate_increasing(
                        row.target_correlation.minimum,
                        row.target_correlation.maximum,
                        fraction,
                    ),
                ),
                StressComponent(
                    family=StressFamily.SUSTAINED_CUMULATIVE_SHOCK,
                    start_date=self.market_dates[shock_start],
                    duration_rows=duration,
                    shocks={
                        Symbol.SPY: _interpolate_adverse(
                            row.spy_cumulative_shock.minimum,
                            row.spy_cumulative_shock.maximum,
                            fraction,
                        ),
                        Symbol.TLT: _interpolate_adverse(
                            row.tlt_cumulative_shock.minimum,
                            row.tlt_cumulative_shock.maximum,
                            fraction,
                        ),
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
                raise OfflineConfigError("dataset has no resolvable monthly rebalance gap")
            rebalance_position = rebalance_positions[ordinal % len(rebalance_positions)]
            offset = row.rebalance_offsets_rows[ordinal % len(row.rebalance_offsets_rows)]
            return (
                StressComponent(
                    family=StressFamily.ONE_DAY_GAP,
                    date=self.market_dates[rebalance_position + offset],
                    shocks={
                        Symbol.SPY: _interpolate_adverse(
                            row.spy_one_day_gap.minimum,
                            row.spy_one_day_gap.maximum,
                            fraction,
                        ),
                        Symbol.TLT: _interpolate_adverse(
                            row.tlt_one_day_gap.minimum,
                            row.tlt_one_day_gap.maximum,
                            fraction,
                        ),
                    },
                ),
            )
        if isinstance(row, VolatilityRegimeHypothesisPolicy):
            duration = row.stress_duration_rows.minimum + round(
                (row.stress_duration_rows.maximum - row.stress_duration_rows.minimum) * fraction
            )
            return (
                StressComponent(
                    family=StressFamily.VOLATILITY_MULTIPLIER,
                    start_date=self.market_dates[1],
                    end_date=self.market_dates[duration],
                    symbols=(Symbol.SPY, Symbol.TLT),
                    volatility_multiplier=_interpolate_increasing(
                        row.volatility_multiplier.minimum,
                        row.volatility_multiplier.maximum,
                        fraction,
                    ),
                ),
            )
        if isinstance(row, TradingFrictionHypothesisPolicy):
            return (
                StressComponent(
                    family=StressFamily.TRANSACTION_COST_MULTIPLIER,
                    transaction_cost_multiplier=_interpolate_increasing(
                        row.transaction_cost_multiplier.minimum,
                        row.transaction_cost_multiplier.maximum,
                        fraction,
                    ),
                ),
            )
        raise OfflineConfigError("unsupported hypothesis row")

    def _component(
        self,
        family: StressFamily,
        ordinal: int,
        fraction: float,
    ) -> StressComponent:
        ranges = self.policy.numeric_ranges
        if family is StressFamily.ONE_DAY_GAP:
            shock = _interpolate_adverse(
                ranges.one_day_gap_shock.minimum,
                ranges.one_day_gap_shock.maximum,
                fraction,
            )
            position = 1 + ordinal % (len(self.market_dates) - 1)
            return StressComponent(
                family=family,
                date=self.market_dates[position],
                shocks={Symbol.SPY: shock, Symbol.TLT: shock},
            )
        if family is StressFamily.SUSTAINED_CUMULATIVE_SHOCK:
            duration_range = ranges.sustained_duration_rows
            maximum_duration = min(duration_range.maximum, len(self.market_dates) - 1)
            if maximum_duration < duration_range.minimum:
                raise OfflineConfigError("dataset is too short for the duration policy")
            duration_span = maximum_duration - duration_range.minimum
            duration = duration_range.minimum + round(duration_span * fraction)
            latest_start = len(self.market_dates) - duration
            start_position = max(1, round(latest_start * 0.60))
            shock = _interpolate_adverse(
                ranges.sustained_cumulative_shock.minimum,
                ranges.sustained_cumulative_shock.maximum,
                fraction,
            )
            return StressComponent(
                family=family,
                start_date=self.market_dates[start_position],
                duration_rows=duration,
                shocks={Symbol.SPY: shock, Symbol.TLT: shock},
            )
        if family is StressFamily.VOLATILITY_MULTIPLIER:
            multiplier = _interpolate_increasing(
                ranges.volatility_multiplier.minimum,
                ranges.volatility_multiplier.maximum,
                fraction,
            )
            regime_end = max(2, (len(self.market_dates) - 1) // 2)
            return StressComponent(
                family=family,
                start_date=self.market_dates[1],
                end_date=self.market_dates[regime_end],
                symbols=(Symbol.SPY, Symbol.TLT),
                volatility_multiplier=multiplier,
            )
        if family is StressFamily.CORRELATION_TARGET:
            target = _interpolate_increasing(
                ranges.target_correlation.minimum,
                ranges.target_correlation.maximum,
                fraction,
            )
            regime_end = max(2, (len(self.market_dates) - 1) // 2)
            return StressComponent(
                family=family,
                start_date=self.market_dates[1],
                end_date=self.market_dates[regime_end],
                target_correlation=target,
            )
        if family is StressFamily.TRANSACTION_COST_MULTIPLIER:
            multiplier = _interpolate_increasing(
                ranges.transaction_cost_multiplier.minimum,
                ranges.transaction_cost_multiplier.maximum,
                fraction,
            )
            return StressComponent(
                family=family,
                transaction_cost_multiplier=multiplier,
            )
        raise OfflineConfigError(f"unsupported offline stress family: {family.value}")


@dataclass(frozen=True)
class DeterministicOfflineReportClient:
    """Return typed mechanism labels only; never calculate or supply report numbers."""

    def write(
        self,
        *,
        prompt: str,
        evidence_summary: DefenderEvidenceSummary,
    ) -> str:
        del prompt
        assessments = tuple(
            CausalClaimAssessment(
                scenario_id=item.scenario_id,
                status=(
                    CausalClaimStatus.VERIFIED
                    if item.replay_verdict is DefenderVerdictValue.REPRODUCED
                    else CausalClaimStatus.UNVERIFIABLE
                ),
                claimed_families=(
                    item.scenario_families
                    if item.replay_verdict is DefenderVerdictValue.REPRODUCED
                    else ()
                ),
                reason=(
                    "The label is limited to the typed stress mechanisms and independently "
                    "replayed engine evidence."
                ),
            )
            for item in evidence_summary.items
        )
        return DefenderNarrativeBatch(assessments=assessments).model_dump_json()


def _artifact_reference(content: bytes) -> ArtifactReference:
    return ArtifactReference(
        sha256=hashlib.sha256(content).hexdigest(),
        byte_length=len(content),
    )


def _write_new_file(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise OfflineArtifactError(f"could not create offline artifact: {path.name}") from error


def _json_lines(records: tuple[ContractModel, ...]) -> bytes:
    return b"".join(canonical_json_bytes(record) for record in records)


def _relative_file_names(directory: Path) -> frozenset[str]:
    try:
        return frozenset(
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        )
    except OSError as error:
        raise OfflineArtifactIntegrityError("cannot inspect offline artifact bundle") from error


def _read_typed_json_lines(
    path: Path,
) -> tuple[ScenarioEvaluationRecord, ...]:
    adapter = TypeAdapter(ScenarioEvaluationRecord)
    try:
        return tuple(adapter.validate_json(line) for line in path.read_bytes().splitlines())
    except (OSError, ValidationError) as error:
        raise OfflineArtifactIntegrityError(
            f"invalid replay JSONL artifact: {path.name}"
        ) from error


def _all_data_hashes(
    attack_directory: Path,
    report: FailureReport,
    replays: tuple[ScenarioEvaluationRecord, ...],
) -> set[str]:
    hashes = {report.data_sha256}
    try:
        for line in (attack_directory / "results.jsonl").read_bytes().splitlines():
            hashes.add(ScenarioEvaluationRecord.model_validate_json(line).result.data_sha256)
    except (OSError, ValidationError) as error:
        raise OfflineArtifactIntegrityError("invalid attacker result evidence") from error
    hashes.update(record.result.data_sha256 for record in replays)
    hashes.update(result.data_sha256 for result in report.verified_results)
    return hashes


def verify_offline_artifacts(directory: Path) -> OfflineRunArtifact:
    """Verify exact files, hashes, schemas, budgets, replay, and hash continuity."""
    directory = directory.resolve()
    if _relative_file_names(directory) != OFFLINE_REQUIRED_ARTIFACT_FILES:
        raise OfflineArtifactIntegrityError(
            "offline bundle does not contain exactly the required artifacts"
        )
    try:
        index_bytes = (directory / "offline_run.json").read_bytes()
        index = OfflineRunArtifact.model_validate_json(index_bytes)
        defender = DefenderArtifact.model_validate_json(
            (directory / "defender_verdicts.json").read_bytes()
        )
        report = FailureReport.model_validate_json((directory / "failure_report.json").read_bytes())
        telemetry = RunTelemetry.model_validate_json((directory / "telemetry.json").read_bytes())
    except (OSError, ValidationError) as error:
        raise OfflineArtifactIntegrityError("offline typed artifact validation failed") from error
    if index_bytes != canonical_json_bytes(index):
        raise OfflineArtifactIntegrityError("offline_run.json is not canonical")
    for name, reference in index.artifact_files.items():
        try:
            content = (directory / Path(name)).read_bytes()
        except OSError as error:
            raise OfflineArtifactIntegrityError(f"cannot read indexed artifact: {name}") from error
        if len(content) != reference.byte_length:
            raise OfflineArtifactIntegrityError(f"artifact byte length mismatch: {name}")
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise OfflineArtifactIntegrityError(f"artifact hash mismatch: {name}")

    attack_directory = directory / OFFLINE_ATTACK_DIRECTORY
    try:
        attack_index = verify_run_artifacts(attack_directory)
        top_failures = TopFailuresArtifact.model_validate_json(
            (attack_directory / "top_failures.json").read_bytes()
        )
    except ArtifactIntegrityError as error:
        raise OfflineArtifactIntegrityError("nested attacker bundle failed verification") from error
    except (OSError, ValidationError) as error:
        raise OfflineArtifactIntegrityError("invalid nested top-failure evidence") from error
    replays = _read_typed_json_lines(directory / "replay_results.jsonl")
    if attack_index.experiment != index.experiment:
        raise OfflineArtifactIntegrityError("offline and attacker experiment configs differ")
    if attack_index.config_sha256 != index.config_sha256:
        raise OfflineArtifactIntegrityError("configuration hash continuity failed")
    if attack_index.dataset_manifest_sha256 != index.dataset_manifest_sha256:
        raise OfflineArtifactIntegrityError("dataset manifest hash continuity failed")
    if attack_index.budget != index.budget:
        raise OfflineArtifactIntegrityError("recorded attack budgets differ")
    if not attack_index.attack_completed:
        raise OfflineArtifactIntegrityError("attacker did not complete cleanly")
    if attack_index.candidate_slots_consumed != index.candidate_slots_consumed:
        raise OfflineArtifactIntegrityError("candidate accounting differs")
    if attack_index.top_failure_count != index.top_failure_count:
        raise OfflineArtifactIntegrityError("top-failure accounting differs")

    if report.config_sha256 != index.config_sha256:
        raise OfflineArtifactIntegrityError("report configuration hash differs")
    if (
        telemetry.config_sha256 != index.config_sha256
        or telemetry.dataset_manifest_sha256 != index.dataset_manifest_sha256
        or telemetry.experiment_id != index.experiment.experiment_id
    ):
        raise OfflineArtifactIntegrityError("telemetry provenance differs from the final index")
    if (
        report.experiment_id != index.experiment.experiment_id
        or report.code_version != index.experiment.code_version
        or report.seed != index.experiment.seed
    ):
        raise OfflineArtifactIntegrityError("report experiment provenance differs")
    if report.defender_verdicts != defender.verdicts:
        raise OfflineArtifactIntegrityError("report and defender verdicts differ")
    if any(verdict.verdict is not DefenderVerdictValue.REPRODUCED for verdict in defender.verdicts):
        raise OfflineReplayError("one or more defender verdicts were not reproduced")

    replay_ids = tuple(record.result.scenario_id for record in replays)
    report_ids = tuple(result.scenario_id for result in report.verified_results)
    verdict_ids = tuple(verdict.scenario_id for verdict in defender.verdicts)
    top_failure_ids = tuple(record.result.scenario_id for record in top_failures.failures)
    if replay_ids != report_ids or replay_ids != verdict_ids or replay_ids != top_failure_ids:
        raise OfflineArtifactIntegrityError(
            "top failure, replay, report, and verdict IDs do not align"
        )
    if len(replays) != index.replay_count:
        raise OfflineArtifactIntegrityError("replay count differs from the final index")
    if len(report.verified_results) != index.verified_failure_count:
        raise OfflineArtifactIntegrityError("verified result count differs from the final index")
    for replay, reported in zip(replays, report.verified_results, strict=True):
        replay_payload = replay.result.model_dump(mode="python")
        replay_payload["rank"] = reported.rank
        ranked_replay = StressResult.model_validate(replay_payload)
        if ranked_replay != reported:
            raise OfflineArtifactIntegrityError("reported result differs from replay evidence")
    if _all_data_hashes(attack_directory, report, replays) != {index.experiment.data_sha256}:
        raise OfflineArtifactIntegrityError("dataset hash continuity failed")

    try:
        manifest_bytes = (attack_directory / "dataset_manifest.json").read_bytes()
        manifest = DataManifest.model_validate_json(manifest_bytes)
    except (OSError, ValidationError) as error:
        raise OfflineArtifactIntegrityError("invalid nested dataset manifest") from error
    if hashlib.sha256(manifest_bytes).hexdigest() != index.dataset_manifest_sha256:
        raise OfflineArtifactIntegrityError("manifest file hash differs from the final index")
    if manifest_bytes != canonical_manifest_bytes(manifest):
        raise OfflineArtifactIntegrityError("nested dataset manifest is not canonical")
    if (
        manifest.dataset_id != index.experiment.dataset_id
        or manifest.sha256 != index.experiment.data_sha256
    ):
        raise OfflineArtifactIntegrityError("manifest and experiment dataset identities differ")
    markdown = (directory / "failure_report.md").read_text(encoding="utf-8")
    if not markdown.startswith(
        "# Defender failure report\n\n> **Research only; not investment advice.**"
    ):
        raise OfflineArtifactIntegrityError("verified report is missing its opening notice")
    return index


def _publish_offline_artifacts(
    *,
    staging: Path,
    destination: Path,
    attack_index: ExperimentArtifact,
    attack_run: AttackRun,
    defense: DefenseRun,
    provider_configuration: ModelProviderConfiguration,
) -> OfflineRunArtifact:
    defender_artifact = DefenderArtifact(
        verdicts=defense.verdicts,
        accepted_assessments=defense.accepted_assessments,
        narrative_rejections=defense.narrative_rejections,
    )
    telemetry = build_run_telemetry(
        attack_run=attack_run,
        defense=defense,
        provider_configuration=provider_configuration,
    )
    root_content = {
        "defender_verdicts.json": canonical_json_bytes(defender_artifact),
        "failure_report.json": canonical_json_bytes(defense.report),
        "failure_report.md": defense.markdown.encode("utf-8"),
        "replay_results.jsonl": _json_lines(defense.replay_records),
        "telemetry.json": telemetry.canonical_json_bytes(),
    }
    for name, content in root_content.items():
        _write_new_file(staging / name, content)

    references: dict[str, ArtifactReference] = {}
    for name in sorted(OFFLINE_HASHED_ARTIFACT_FILES):
        try:
            references[name] = _artifact_reference((staging / Path(name)).read_bytes())
        except OSError as error:
            raise OfflineArtifactError(f"cannot index offline artifact: {name}") from error
    index = OfflineRunArtifact(
        experiment=attack_index.experiment,
        config_sha256=attack_index.config_sha256,
        dataset_manifest_sha256=attack_index.dataset_manifest_sha256,
        budget=attack_index.budget,
        candidate_slots_consumed=attack_index.candidate_slots_consumed,
        top_failure_count=attack_index.top_failure_count,
        replay_count=len(defense.replay_records),
        verified_failure_count=len(defense.report.verified_results),
        artifact_files=references,
    )
    _write_new_file(staging / "offline_run.json", canonical_json_bytes(index))
    verify_offline_artifacts(staging)
    try:
        os.replace(staging, destination)
    except OSError as error:
        raise OfflineArtifactError("atomic offline artifact publication failed") from error
    return verify_offline_artifacts(destination)


def run_offline_experiment(
    *,
    config_path: Path,
    manifest_path: Path,
    artifact_directory: Path,
) -> OfflineRunArtifact:
    """Execute baseline, bounded attack, top replay, and verified report locally."""
    config = load_offline_config(config_path)
    manifest_path = manifest_path.resolve()
    store = LocalDatasetStore(manifest_path.parent.parent)
    try:
        dataset = store.validate(manifest_path)
        experiment = config.bind_dataset(dataset)
        strategy = strategy_from_spec(
            experiment.strategy,
            experiment.numeric_tolerance,
            None,
        )
    except (DatasetVerificationError, StrategyError, ValidationError) as error:
        raise OfflineConfigError(f"offline inputs failed validation: {error}") from error

    artifact_directory = artifact_directory.resolve()
    if artifact_directory.exists():
        raise OfflineArtifactError(f"artifact directory already exists: {artifact_directory}")
    artifact_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{artifact_directory.name}.tmp-",
            dir=str(artifact_directory.parent),
        )
    ).resolve()
    try:
        proposer = build_scenario_proposer(
            config.model_provider,
            deterministic=DeterministicOfflineScenarioClient.from_dataset(
                dataset,
                config.attack_policy,
                experiment.max_total_scenarios,
            ),
        )
        attack_run = AttackerService(proposer).run(
            dataset=dataset,
            strategy=strategy,
            experiment=experiment,
            policy=config.attack_policy,
            artifact_directory=staging / OFFLINE_ATTACK_DIRECTORY,
        )
        attack_index = verify_run_artifacts(staging / OFFLINE_ATTACK_DIRECTORY)
        if not attack_run.attack_completed or not attack_index.attack_completed:
            raise OfflineRunError(
                f"bounded attacker did not complete cleanly: {attack_run.stop_reason.value}"
            )
        defense = DefenderService(
            store=store,
            report_writer=build_report_writer(
                config.model_provider,
                deterministic=DeterministicOfflineReportClient(),
            ),
        ).defend(
            attack_run=attack_run,
            manifest_path=manifest_path,
        )
        if len(defense.verdicts) != len(attack_run.top_failures) or any(
            verdict.verdict is not DefenderVerdictValue.REPRODUCED for verdict in defense.verdicts
        ):
            raise OfflineReplayError("defender did not reproduce every selected attacker failure")
        return _publish_offline_artifacts(
            staging=staging,
            destination=artifact_directory,
            attack_index=attack_index,
            attack_run=attack_run,
            defense=defense,
            provider_configuration=config.model_provider,
        )
    except (AttackError, ArtifactIntegrityError, ModelProviderConfigurationError) as error:
        raise OfflineRunError(f"offline attack failed: {error}") from error
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def offline_artifact_names() -> tuple[str, ...]:
    """Return the exact portable artifact-name contract for tests and CLI output."""
    return tuple(sorted(OFFLINE_REQUIRED_ARTIFACT_FILES))


def hash_continuity(index: OfflineRunArtifact) -> Mapping[str, str]:
    """Expose the two provenance hashes without recalculating market evidence."""
    return {
        "data_sha256": index.experiment.data_sha256,
        "dataset_manifest_sha256": index.dataset_manifest_sha256,
        "config_sha256": index.config_sha256,
    }
