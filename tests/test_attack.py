"""Deterministic acceptance tests for Gate 6 bounded attack orchestration."""

from __future__ import annotations

import ast
import hashlib
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pytest

import strategy_redteam.artifacts as artifacts_module
import strategy_redteam.attack as attack_module
from strategy_redteam import (
    DEFAULT_NUMERIC_TOLERANCE,
    MAX_CANDIDATES_PER_ROUND,
    MAX_ROUNDS,
    MAX_TOTAL_SCENARIOS,
    REQUIRED_ARTIFACT_FILES,
    TOP_K,
    ArtifactIntegrityError,
    ArtifactWriteError,
    AttackBudget,
    AttackBudgetExceeded,
    AttackPolicy,
    AttackPolicyError,
    DataManifest,
    DeterministicOfflineProposer,
    ExperimentSpec,
    FailureRule,
    FixedMonthly6040Strategy,
    ProposalDecision,
    RejectionCode,
    StoredDataset,
    StrategySpec,
    StressComponent,
    StressResult,
    StressScenario,
    canonical_json_sha256,
    load_attack_policy,
    run_attack,
    verify_run_artifacts,
)
from strategy_redteam.data import canonical_manifest_bytes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "config" / "attack-policy-v1.yaml"


@pytest.fixture
def attack_context() -> tuple[StoredDataset, FixedMonthly6040Strategy]:
    """Return a non-singular fixed daily dataset and the built-in strategy."""
    dates = pd.date_range("2024-01-02", periods=60, freq="B", tz="UTC", name="date")
    spy_pattern = np.asarray([0.004, -0.003, 0.002, -0.001, 0.003], dtype=np.float64)
    tlt_pattern = np.asarray([-0.001, 0.002, -0.002, 0.003, 0.001], dtype=np.float64)
    spy_returns = np.resize(spy_pattern, len(dates) - 1)
    tlt_returns = np.resize(tlt_pattern, len(dates) - 1)
    prices = np.column_stack(
        (
            np.concatenate(([100.0], 100.0 * np.cumprod(1.0 + spy_returns))),
            np.concatenate(([100.0], 100.0 * np.cumprod(1.0 + tlt_returns))),
        )
    )
    columns = pd.MultiIndex.from_tuples(
        [("SPY", "close"), ("TLT", "close")],
        names=("symbol", "field"),
    )
    data = pd.DataFrame(prices, index=dates, columns=columns)
    manifest = DataManifest(
        dataset_id="gate-6-fixed-data",
        provider="fixed-test-provider",
        source_identifiers={"SPY": "fixed:SPY", "TLT": "fixed:TLT"},
        symbols=("SPY", "TLT"),
        requested_start_date=dates[0].date(),
        requested_end_date=dates[-1].date(),
        start_date=dates[0].date(),
        end_date=dates[-1].date(),
        adjustment_policy="splits_and_distributions",
        calendar_policy="fixed common business dates",
        missing_data_policy="reject",
        row_count=len(dates),
        columns=("date", "SPY.close", "TLT.close"),
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        media_type="application/vnd.apache.parquet",
        byte_length=1,
        sha256="d" * 64,
    )
    manifest_sha256 = hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
    dataset = StoredDataset(
        manifest=manifest,
        data=data,
        dataset_path=Path("gate-6-fixed.parquet"),
        manifest_path=Path("gate-6-fixed.manifest.json"),
        manifest_sha256=manifest_sha256,
    )
    strategy_spec = StrategySpec(
        strategy_id="monthly-60-40-gate-6",
        kind="monthly_60_40",
        symbols=("SPY", "TLT"),
        target_weights={"SPY": 0.6, "TLT": 0.4},
        rebalance_frequency="month_start",
    )
    return dataset, FixedMonthly6040Strategy(strategy_spec)


def make_policy(
    *,
    minimum_failures: int = 3,
    minimum_excess: float = 0.0,
    shock_minimum: float = -0.90,
    shock_maximum: float = 0.10,
) -> AttackPolicy:
    """Return a one-family policy with all numeric domains explicitly bounded."""
    return AttackPolicy.model_validate(
        {
            "schema_version": "1.0",
            "policy_id": "gate-6-test-policy-v1",
            "allowed_families": ["one_day_gap"],
            "numeric_ranges": {
                "schema_version": "1.0",
                "one_day_gap_shock": {
                    "schema_version": "1.0",
                    "minimum": shock_minimum,
                    "maximum": shock_maximum,
                },
                "sustained_cumulative_shock": {
                    "schema_version": "1.0",
                    "minimum": -0.50,
                    "maximum": 0.10,
                },
                "sustained_duration_rows": {
                    "schema_version": "1.0",
                    "minimum": 2,
                    "maximum": 20,
                },
                "volatility_multiplier": {
                    "schema_version": "1.0",
                    "minimum": 1.0,
                    "maximum": 4.0,
                },
                "target_correlation": {
                    "schema_version": "1.0",
                    "minimum": -0.9,
                    "maximum": 0.9,
                },
                "transaction_cost_multiplier": {
                    "schema_version": "1.0",
                    "minimum": 1.0,
                    "maximum": 5.0,
                },
            },
            "evidence_condition": {
                "schema_version": "1.0",
                "minimum_failure_scenarios": minimum_failures,
                "minimum_breach_count": 1,
                "minimum_maximum_normalized_excess": minimum_excess,
            },
        }
    )


def make_experiment(
    dataset: StoredDataset,
    strategy: FixedMonthly6040Strategy,
    **updates: object,
) -> ExperimentSpec:
    """Build a bounded one-rule Gate 6 experiment."""
    values: dict[str, object] = {
        "experiment_id": "gate-6-experiment",
        "dataset_id": dataset.manifest.dataset_id,
        "data_sha256": dataset.manifest.sha256,
        "strategy": strategy.spec,
        "failure_rules": (
            FailureRule(
                rule_id="drawdown-limit",
                family="maximum_drawdown",
                threshold=0.10,
                window_rows=None,
            ),
        ),
        "seed": 41,
        "timeout_seconds": 60.0,
        "code_version": "gate-6-test",
        "numeric_tolerance": 1e-9,
        "max_rounds": 3,
        "max_candidates_per_round": 8,
        "max_total_scenarios": 24,
        "top_k": 3,
    }
    values.update(updates)
    return ExperimentSpec.model_validate(values)


def gap_scenario(
    dataset: StoredDataset,
    scenario_id: str,
    shock: float,
    *,
    position: int = 10,
    hypothesis: str = "Typed deterministic gap candidate.",
) -> StressScenario:
    """Create a full-window two-asset gap scenario."""
    return StressScenario(
        scenario_id=scenario_id,
        evaluation_start=dataset.data.index[0].date(),
        evaluation_end=dataset.data.index[-1].date(),
        components=(
            StressComponent(
                family="one_day_gap",
                date=dataset.data.index[position].date(),
                shocks={"SPY": shock, "TLT": shock},
            ),
        ),
        hypothesis=hypothesis,
    )


@dataclass
class SequentialProposer:
    """Test proposer that respects the runner's requested batch size."""

    candidates: list[StressScenario]
    position: int = 0
    requests: list[int] = field(default_factory=list)

    def propose(
        self,
        *,
        round_number: int,
        max_candidates: int,
        prior_results: tuple[StressResult, ...],
    ) -> list[StressScenario]:
        del round_number, prior_results
        self.requests.append(max_candidates)
        selected = self.candidates[self.position : self.position + max_candidates]
        self.position += len(selected)
        return selected


@dataclass
class BatchProposer:
    """Return predefined raw or typed batches without silently slicing them."""

    batches: list[list[Any]]
    calls: int = 0

    def propose(
        self,
        *,
        round_number: int,
        max_candidates: int,
        prior_results: tuple[StressResult, ...],
    ) -> list[Any]:
        del round_number, max_candidates, prior_results
        batch = self.batches[self.calls] if self.calls < len(self.batches) else []
        self.calls += 1
        return batch


@dataclass
class ManualClock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value


@dataclass
class AdvancingProposer:
    candidates: list[StressScenario]
    clock: ManualClock

    def propose(
        self,
        *,
        round_number: int,
        max_candidates: int,
        prior_results: tuple[StressResult, ...],
    ) -> list[StressScenario]:
        del round_number, prior_results
        self.clock.value = 2.0
        return self.candidates[:max_candidates]


def test_versioned_yaml_policy_is_safe_and_rejects_duplicate_keys(tmp_path: Path) -> None:
    """The checked-in policy loads, while ambiguous YAML cannot overwrite values."""
    policy = load_attack_policy(DEFAULT_POLICY_PATH)
    assert policy.schema_version == "1.0"
    assert policy.policy_id == "offline-bounded-v1"
    assert len(policy.allowed_families) == 5

    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "schema_version: '1.0'\nschema_version: '1.0'\npolicy_id: duplicate\n",
        encoding="utf-8",
    )
    with pytest.raises(AttackPolicyError, match="duplicate YAML key"):
        load_attack_policy(duplicate)


def test_numeric_tolerance_is_required_config_and_changes_config_hash(
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """Configuration provenance commits to the exact configurable tolerance."""
    dataset, strategy = attack_context
    default = make_experiment(dataset, strategy)
    alternate = make_experiment(dataset, strategy, numeric_tolerance=1e-8)

    assert default.numeric_tolerance == DEFAULT_NUMERIC_TOLERANCE
    assert canonical_json_sha256(default) != canonical_json_sha256(alternate)


def test_attack_budget_enforces_every_hard_limit_and_deadline() -> None:
    """Rounds, batch size, total scenarios, top-k, capacity, and timeout are hard."""
    clock = ManualClock()
    budget = AttackBudget(
        max_rounds=MAX_ROUNDS,
        max_candidates_per_round=MAX_CANDIDATES_PER_ROUND,
        max_total_scenarios=MAX_TOTAL_SCENARIOS,
        top_k=TOP_K,
        timeout_seconds=1.0,
        clock=clock,
    )
    budget.start_round()
    budget.reserve_batch(MAX_CANDIDATES_PER_ROUND)
    assert budget.remaining_scenarios == 16
    assert not budget.deadline_reached()
    clock.value = 1.0
    assert budget.deadline_reached()

    invalid_limits = (
        {"max_rounds": 4},
        {"max_candidates_per_round": 9},
        {"max_total_scenarios": 25},
        {"top_k": 4},
        {"max_rounds": 1, "max_candidates_per_round": 1, "max_total_scenarios": 2},
    )
    for invalid in invalid_limits:
        values = {
            "max_rounds": 3,
            "max_candidates_per_round": 8,
            "max_total_scenarios": 24,
            "top_k": 3,
            "timeout_seconds": 1.0,
        }
        values.update(invalid)
        with pytest.raises(AttackBudgetExceeded):
            AttackBudget(**values)


def test_runner_consumes_three_by_eight_and_stops_at_24(
    tmp_path: Path,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """The runner never requests a fourth round or a ninth candidate."""
    dataset, strategy = attack_context
    candidates = [
        gap_scenario(dataset, f"mild-{index:02d}", -0.020 - index * 0.001, position=2 + index)
        for index in range(MAX_TOTAL_SCENARIOS)
    ]
    proposer = SequentialProposer(candidates)
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=make_experiment(dataset, strategy),
        policy=make_policy(),
        proposer=proposer,
        artifact_directory=tmp_path / "bounded-24",
    )

    assert proposer.requests == [8, 8, 8]
    assert run.rounds_started == 3
    assert run.candidate_slots_consumed == 24
    assert run.evaluated_scenarios == 24
    assert run.stop_reason.value == "max_total_scenarios_reached"
    assert len(run.top_failures) <= TOP_K


def test_total_budget_reduces_the_last_batch_request(
    tmp_path: Path,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """A configured total of nine permits one full batch and one single candidate."""
    dataset, strategy = attack_context
    proposer = SequentialProposer(
        [
            gap_scenario(dataset, f"total-{index}", -0.03 - index * 0.001, position=5 + index)
            for index in range(9)
        ]
    )
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=make_experiment(dataset, strategy, max_total_scenarios=9),
        policy=make_policy(),
        proposer=proposer,
        artifact_directory=tmp_path / "bounded-9",
    )
    assert proposer.requests == [8, 1]
    assert run.candidate_slots_consumed == 9
    assert run.rounds_started == 2
    assert run.stop_reason.value == "max_total_scenarios_reached"


def test_oversized_proposer_batch_is_not_iterated(
    tmp_path: Path,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """A ninth proposal terminates the run before any candidate slot is consumed."""
    dataset, strategy = attack_context
    oversized = [
        gap_scenario(dataset, f"oversized-{index}", -0.02 - index * 0.001, position=2 + index)
        for index in range(9)
    ]
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=make_experiment(dataset, strategy),
        policy=make_policy(),
        proposer=BatchProposer([oversized]),
        artifact_directory=tmp_path / "oversized",
    )
    assert run.stop_reason.value == "proposer_budget_violation"
    assert run.candidate_slots_consumed == 0
    assert run.evaluations == ()
    assert not run.attack_completed


def test_timeout_stops_evaluation_and_publishes_honest_complete_bundle(
    tmp_path: Path,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """Candidates returned after the deadline receive typed timeout rejections."""
    dataset, strategy = attack_context
    clock = ManualClock()
    candidates = [
        gap_scenario(dataset, "timeout-1", -0.2),
        gap_scenario(dataset, "timeout-2", -0.3, position=11),
    ]
    destination = tmp_path / "timeout"
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=make_experiment(dataset, strategy, timeout_seconds=1.0),
        policy=make_policy(),
        proposer=AdvancingProposer(candidates, clock),
        artifact_directory=destination,
        clock=clock,
    )
    index = verify_run_artifacts(destination)
    assert run.stop_reason.value == "timeout"
    assert run.evaluated_scenarios == 0
    assert run.rejected_scenarios == 2
    assert all(
        record.result.rejection_code is RejectionCode.TIMEOUT for record in run.evaluations
    )
    assert not index.attack_completed
    assert index.artifact_bundle_complete


def test_evidence_finishing_after_deadline_is_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """An in-flight deterministic call cannot publish evidence after the deadline."""
    dataset, strategy = attack_context
    clock = ManualClock()
    original = attack_module._evaluate_scenario

    def finish_late(**kwargs: Any) -> Any:
        result = original(**kwargs)
        clock.value = 2.0
        return result

    monkeypatch.setattr(attack_module, "_evaluate_scenario", finish_late)
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=make_experiment(
            dataset,
            strategy,
            timeout_seconds=1.0,
            max_rounds=1,
            max_candidates_per_round=1,
            max_total_scenarios=1,
            top_k=1,
        ),
        policy=make_policy(),
        proposer=BatchProposer([[gap_scenario(dataset, "late-evidence", -0.30)]]),
        artifact_directory=tmp_path / "late-evidence",
        clock=clock,
    )
    assert run.stop_reason.value == "timeout"
    assert run.evaluated_scenarios == 0
    assert run.top_failures == ()
    assert run.evaluations[0].result.rejection_code is RejectionCode.TIMEOUT


def test_schema_policy_and_semantic_duplicates_are_rejected_without_clamping(
    tmp_path: Path,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """Invalid candidates consume slots and preserve the original out-of-policy value."""
    dataset, strategy = attack_context
    base = gap_scenario(dataset, "base", -0.20)
    equivalent = gap_scenario(
        dataset,
        "equivalent",
        -0.20,
        hypothesis="Different inert narrative, identical numeric semantics.",
    )
    outside = gap_scenario(dataset, "outside", -0.80, position=12)
    invalid = {
        **gap_scenario(dataset, "invalid-extra", -0.25, position=13).model_dump(mode="json"),
        "unknown_numeric_instruction": 99,
    }
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=make_experiment(
            dataset,
            strategy,
            max_rounds=1,
            max_candidates_per_round=4,
            max_total_scenarios=4,
        ),
        policy=make_policy(shock_minimum=-0.50),
        proposer=BatchProposer([[base, equivalent, outside, invalid]]),
        artifact_directory=tmp_path / "validation",
    )

    assert run.candidate_slots_consumed == 4
    assert run.evaluated_scenarios == 1
    assert run.rejected_scenarios == 3
    assert run.proposals[1].rejection_code is RejectionCode.DUPLICATE_SCENARIO
    assert run.proposals[2].rejection_code is RejectionCode.INVALID_PARAMETER
    assert run.proposals[2].scenario is not None
    assert run.proposals[2].scenario.components[0].shocks == {
        "SPY": -0.80,
        "TLT": -0.80,
    }
    assert run.proposals[3].decision is ProposalDecision.REJECTED
    assert run.proposals[3].scenario is None


def test_evidence_condition_stops_before_a_later_round(
    tmp_path: Path,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """One qualifying failure ends the attack after its current bounded batch."""
    dataset, strategy = attack_context
    first = [gap_scenario(dataset, "qualifying", -0.30)]
    second = [gap_scenario(dataset, "must-not-run", -0.40, position=11)]
    proposer = BatchProposer([first, second])
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=make_experiment(dataset, strategy),
        policy=make_policy(minimum_failures=1),
        proposer=proposer,
        artifact_directory=tmp_path / "early-stop",
    )
    assert proposer.calls == 1
    assert run.rounds_started == 1
    assert run.evidence_condition_met
    assert run.stop_reason.value == "evidence_condition_met"


def test_ranking_is_bounded_to_top_three_and_report_explains_evidence(
    tmp_path: Path,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """Ranking uses the tuple, while prose exposes mechanism and components."""
    dataset, strategy = attack_context
    shocks = (-0.18, -0.25, -0.32, -0.40, -0.50)
    candidates = [
        gap_scenario(dataset, f"failure-{index}", shock, position=10 + index)
        for index, shock in enumerate(shocks)
    ]
    destination = tmp_path / "ranked"
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=make_experiment(
            dataset,
            strategy,
            max_rounds=1,
            max_candidates_per_round=5,
            max_total_scenarios=5,
        ),
        policy=make_policy(minimum_failures=3),
        proposer=BatchProposer([candidates]),
        artifact_directory=destination,
    )
    report = (destination / "failure_report.md").read_text(encoding="utf-8")
    assert len(run.top_failures) == TOP_K
    assert tuple(record.result.rank for record in run.top_failures) == (1, 2, 3)
    assert "Failure mechanism" in report
    assert "Explicit numeric stress" in report
    assert "Worst window" in report
    assert "Linked contributions" in report
    assert "Defender status" in report
    assert "## Limitations" in report
    assert "No composite or magic score" in report


def test_no_failure_artifact_is_honest_and_integrity_checked(
    tmp_path: Path,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """A fixture with no breach emits an empty top list and an explicit negative result."""
    dataset, strategy = attack_context
    destination = tmp_path / "no-failure"
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=make_experiment(
            dataset,
            strategy,
            failure_rules=(
                FailureRule(
                    rule_id="large-drawdown-limit",
                    family="maximum_drawdown",
                    threshold=0.80,
                    window_rows=None,
                ),
            ),
            max_rounds=1,
            max_candidates_per_round=1,
            max_total_scenarios=1,
            top_k=1,
        ),
        policy=make_policy(),
        proposer=BatchProposer([[gap_scenario(dataset, "no-failure", -0.02)]]),
        artifact_directory=destination,
    )
    report = (destination / "failure_report.md").read_text(encoding="utf-8")
    assert run.top_failures == ()
    assert "No configured failure was found" in report
    assert "does not establish robustness" in report
    assert "Not run in Gate 6" in report
    assert set(path.name for path in destination.iterdir()) == REQUIRED_ARTIFACT_FILES
    verify_run_artifacts(destination)

    with (destination / "results.jsonl").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ArtifactIntegrityError, match="mismatch"):
        verify_run_artifacts(destination)


def test_atomic_write_failure_never_exposes_final_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """A staged write failure leaves no directory that could look complete."""
    dataset, strategy = attack_context
    destination = tmp_path / "atomic-failure"
    original = artifacts_module._write_new_file

    def fail_results(path: Path, content: bytes) -> None:
        if path.name == "results.jsonl":
            raise OSError("injected write failure")
        original(path, content)

    monkeypatch.setattr(artifacts_module, "_write_new_file", fail_results)
    with pytest.raises(ArtifactWriteError, match="atomic artifact publication failed"):
        run_attack(
            dataset=dataset,
            strategy=strategy,
            experiment=make_experiment(
                dataset,
                strategy,
                max_rounds=1,
                max_candidates_per_round=1,
                max_total_scenarios=1,
                top_k=1,
            ),
            policy=make_policy(),
            proposer=BatchProposer([[gap_scenario(dataset, "atomic", -0.02)]]),
            artifact_directory=destination,
        )
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".atomic-failure.tmp-*"))


def test_baseline_runs_once_and_repeated_bundles_are_byte_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """Candidate evaluation reuses one baseline and stable inputs produce stable bytes."""
    dataset, strategy = attack_context
    candidates = [
        gap_scenario(dataset, f"stable-{index}", -0.03 - index * 0.01, position=10 + index)
        for index in range(3)
    ]
    calls = 0
    original = attack_module.run_backtest

    def counted_baseline(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(attack_module, "run_backtest", counted_baseline)
    first = tmp_path / "stable-first"
    second = tmp_path / "stable-second"
    experiment = make_experiment(
        dataset,
        strategy,
        max_rounds=1,
        max_candidates_per_round=3,
        max_total_scenarios=3,
    )
    run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=experiment,
        policy=make_policy(),
        proposer=BatchProposer([candidates]),
        artifact_directory=first,
    )
    assert calls == 1

    monkeypatch.setattr(attack_module, "run_backtest", original)
    run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=experiment,
        policy=make_policy(),
        proposer=BatchProposer([candidates]),
        artifact_directory=second,
    )
    for name in REQUIRED_ARTIFACT_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_offline_proposer_is_repeatable_and_policy_valid(
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    """Local development proposals are model-free, seeded, and deterministic."""
    dataset, _ = attack_context
    policy = load_attack_policy(DEFAULT_POLICY_PATH)
    proposer = DeterministicOfflineProposer.from_dataset(dataset, policy, seed=7)
    first = proposer.propose(round_number=1, max_candidates=8, prior_results=())
    second = proposer.propose(round_number=1, max_candidates=8, prior_results=())
    assert first == second
    assert len(first) == MAX_CANDIDATES_PER_ROUND
    for candidate in first:
        assert isinstance(candidate, StressScenario)
        policy.validate_scenario(candidate)


def test_shared_deterministic_candidates_and_catalog_preserve_offline_output(
    attack_context: tuple[StoredDataset, FixedMonthly6040Strategy],
) -> None:
    dataset, _ = attack_context
    policy = load_attack_policy(DEFAULT_POLICY_PATH)
    proposer = DeterministicOfflineProposer.from_dataset(dataset, policy, seed=7)
    expected = proposer.propose(round_number=1, max_candidates=3, prior_results=())
    shared = attack_module.build_deterministic_candidates(
        market_dates=proposer.market_dates,
        policy=policy,
        seed=7,
        round_number=1,
        max_candidates=3,
    )
    catalog = attack_module.build_attack_catalog(shared)
    assert shared == expected
    assert tuple(entry.attack_key for entry in catalog.entries) == (
        "atk_001", "atk_002", "atk_003"
    )
    assert tuple(entry.scenario for entry in catalog.entries) == shared


def test_runner_source_has_only_bounded_round_and_candidate_loops() -> None:
    """The orchestration function has no while, recursion, or open-ended agent loop."""
    tree = ast.parse(inspect.getsource(attack_module.run_attack))
    assert not any(isinstance(node, ast.While) for node in ast.walk(tree))
    loops = [node for node in ast.walk(tree) if isinstance(node, ast.For)]
    assert len(loops) == 2
    assert "range(1, budget.max_rounds + 1)" in ast.unparse(loops[0].iter)
    assert "enumerate(candidates, start=1)" in ast.unparse(loops[1].iter)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert not any(call.func.id == "run_attack" for call in calls)
