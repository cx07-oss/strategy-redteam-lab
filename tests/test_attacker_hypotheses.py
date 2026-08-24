"""Gate 9 acceptance for approved attacker hypotheses and bounded applicability."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pytest

from strategy_redteam import (
    AttackBatch,
    AttackerService,
    AttackHypothesisFamily,
    AttackPolicy,
    AttackPolicyViolation,
    AttackValidationContext,
    DataManifest,
    ExperimentSpec,
    FailureRule,
    FakeScenarioProposer,
    FixedMonthly6040Strategy,
    ResultStatus,
    StoredDataset,
    StrategySpec,
    StressComponent,
    StressResult,
    StressScenario,
    canonical_json_sha256,
    evaluate_scenario,
    load_attack_policy,
    run_attack,
    run_backtest,
    run_stressed_backtest,
)
from strategy_redteam.data import canonical_manifest_bytes

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "attack-policy-v1.yaml"


@pytest.fixture
def hypothesis_context() -> tuple[
    StoredDataset,
    FixedMonthly6040Strategy,
    ExperimentSpec,
    AttackPolicy,
    AttackValidationContext,
]:
    """Return a fixed path with drift, later low variance, and monthly turnover."""
    dates = pd.date_range("2024-01-02", periods=160, freq="B", tz="UTC", name="date")
    earned_rows = len(dates) - 1
    spy_returns = np.resize(
        np.asarray([0.002, -0.001, 0.003, -0.002, 0.001], dtype=np.float64),
        earned_rows,
    )
    tlt_returns = np.resize(
        np.asarray([-0.001, 0.002, -0.002, 0.001, 0.0005, -0.0015], dtype=np.float64),
        earned_rows,
    )
    spy_returns[:21] = np.resize(
        np.asarray([0.018, 0.022, 0.016, 0.024, 0.019], dtype=np.float64),
        21,
    )
    tlt_returns[:21] = np.resize(
        np.asarray([-0.006, 0.002, -0.004, 0.001, -0.003], dtype=np.float64),
        21,
    )
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
    frame = pd.DataFrame(prices, index=dates, columns=columns)
    manifest = DataManifest(
        dataset_id="gate-9-hypothesis-fixture",
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
        sha256="9" * 64,
    )
    dataset = StoredDataset(
        manifest=manifest,
        data=frame,
        dataset_path=Path("gate-9-fixture.parquet"),
        manifest_path=Path("gate-9-fixture.manifest.json"),
        manifest_sha256=hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest(),
    )
    strategy_spec = StrategySpec(
        strategy_id="monthly-60-40-gate-9",
        kind="monthly_60_40",
        symbols=("SPY", "TLT"),
        target_weights={"SPY": 0.6, "TLT": 0.4},
        rebalance_frequency="month_start",
    )
    strategy = FixedMonthly6040Strategy(strategy_spec)
    experiment = ExperimentSpec(
        experiment_id="gate-9-hypothesis-experiment",
        dataset_id=manifest.dataset_id,
        data_sha256=manifest.sha256,
        strategy=strategy_spec,
        failure_rules=(
            FailureRule(
                rule_id="drawdown-limit",
                family="maximum_drawdown",
                threshold=0.08,
                window_rows=None,
            ),
            FailureRule(
                rule_id="volatility-limit",
                family="realized_volatility_multiple",
                threshold=1.50,
                window_rows=20,
            ),
        ),
        seed=20260823,
        timeout_seconds=60.0,
        code_version="gate-9-test",
        numeric_tolerance=1e-9,
        transaction_cost_bps=250.0,
        max_rounds=1,
        max_candidates_per_round=3,
        max_total_scenarios=3,
        top_k=3,
    )
    policy = load_attack_policy(POLICY_PATH)
    baseline = run_backtest(dataset, strategy, 250.0, experiment.numeric_tolerance)
    validation_context = AttackValidationContext(
        strategy_spec=strategy_spec,
        market_dates=tuple(timestamp.date() for timestamp in dates),
        rebalance_dates=tuple(
            timestamp.date() for timestamp in strategy.rebalance_dates(dataset)
        ),
        transaction_cost_bps=250.0,
        positive_turnover_dates=frozenset(
            timestamp.date()
            for timestamp in baseline.turnover.index[baseline.turnover.gt(0.0)]
        ),
    )
    return dataset, strategy, experiment, policy, validation_context


def _inflation_scenario(
    dataset: StoredDataset,
    *,
    window_start: int = 30,
    window_rows: int = 20,
    duration_rows: int = 20,
    spy_shock: float = -0.25,
    tlt_shock: float = -0.20,
) -> StressScenario:
    dates = dataset.data.index
    return StressScenario(
        scenario_id="inflation-fixture",
        evaluation_start=dates[0].date(),
        evaluation_end=dates[-1].date(),
        components=(
            StressComponent(
                family="volatility_multiplier",
                start_date=dates[window_start].date(),
                end_date=dates[window_start + window_rows - 1].date(),
                symbols=("SPY", "TLT"),
                volatility_multiplier=3.0,
            ),
            StressComponent(
                family="correlation_target",
                start_date=dates[window_start].date(),
                end_date=dates[window_start + window_rows - 1].date(),
                target_correlation=0.90,
            ),
            StressComponent(
                family="sustained_cumulative_shock",
                start_date=dates[60].date(),
                duration_rows=duration_rows,
                shocks={"SPY": spy_shock, "TLT": tlt_shock},
            ),
        ),
        hypothesis="Inflation narrative is inert and cannot supply numeric evidence.",
        headline="Typed stock-bond diversification stress",
    )


def _rebalance_scenario(
    dataset: StoredDataset,
    strategy: FixedMonthly6040Strategy,
    *,
    offset: int = -1,
    spy_shock: float = -0.15,
    tlt_shock: float = -0.02,
) -> StressScenario:
    rebalance_date = strategy.rebalance_dates(dataset)[1]
    position = dataset.data.index.get_loc(rebalance_date)
    return StressScenario(
        scenario_id="rebalance-fixture",
        evaluation_start=dataset.data.index[0].date(),
        evaluation_end=dataset.data.index[-1].date(),
        components=(
            StressComponent(
                family="one_day_gap",
                date=dataset.data.index[position + offset].date(),
                shocks={"SPY": spy_shock, "TLT": tlt_shock},
            ),
        ),
        hypothesis="Pre-rebalance prose does not choose the date or calculate the loss.",
        headline="Bounded gap before a predetermined monthly rebalance",
    )


def _friction_scenario(dataset: StoredDataset, multiplier: float = 5.0) -> StressScenario:
    return StressScenario(
        scenario_id="friction-fixture",
        evaluation_start=dataset.data.index[0].date(),
        evaluation_end=dataset.data.index[1].date(),
        components=(
            StressComponent(
                family="transaction_cost_multiplier",
                transaction_cost_multiplier=multiplier,
            ),
        ),
        hypothesis="Cost prose cannot modify the configured basis-point rate.",
        headline="Bounded execution-cost multiplier",
    )


@dataclass
class _OneBatchProposer:
    scenarios: tuple[StressScenario, ...]
    called: bool = False

    def propose(
        self,
        *,
        round_number: int,
        max_candidates: int,
        prior_results: tuple[StressResult, ...],
    ) -> tuple[StressScenario, ...]:
        del round_number, prior_results
        if self.called:
            return ()
        self.called = True
        return self.scenarios[:max_candidates]


def test_applicable_policy_rows_discover_constructed_failures(
    tmp_path: Path,
    hypothesis_context: tuple[
        StoredDataset,
        FixedMonthly6040Strategy,
        ExperimentSpec,
        AttackPolicy,
        AttackValidationContext,
    ],
) -> None:
    """Every applicable row supplies a valid candidate that breaches the fixed fixture."""
    dataset, strategy, experiment, policy, _ = hypothesis_context
    scenarios = (
        _inflation_scenario(dataset),
        _rebalance_scenario(dataset, strategy),
        _friction_scenario(dataset),
    )
    run = run_attack(
        dataset=dataset,
        strategy=strategy,
        experiment=experiment,
        policy=policy,
        proposer=_OneBatchProposer(scenarios),
        artifact_directory=tmp_path / "applicable-discovery",
    )

    assert run.candidate_slots_consumed == 3
    assert run.evaluated_scenarios == 3
    assert all(record.result.breach_count >= 1 for record in run.evaluations)
    assert {
        run.policy.hypothesis_for_scenario(record.scenario).hypothesis_family
        for record in run.evaluations
        if record.scenario is not None
        and run.policy.hypothesis_for_scenario(record.scenario) is not None
    } == {
        AttackHypothesisFamily.INFLATION_CORRELATION_BREAK,
        AttackHypothesisFamily.REBALANCE_TIMING_GAP,
        AttackHypothesisFamily.TRADING_FRICTION_BREAK,
    }

    records = {record.result.scenario_id: record for record in run.evaluations}
    inflation = records["inflation-fixture"]
    achieved_correlation = inflation.component_summaries[1].post_transform_summary
    assert achieved_correlation.spy_tlt_correlation == pytest.approx(
        0.90,
        abs=experiment.numeric_tolerance,
    )
    assert any(
        all(contribution < 0.0 for contribution in window.asset_return_contributions.values())
        for window in inflation.worst_windows
    )

    rebalance = run_stressed_backtest(
        dataset,
        strategy,
        scenarios[1],
        experiment.transaction_cost_bps,
        experiment.numeric_tolerance,
        experiment.failure_rules,
        experiment.seed,
    )
    gap_date = pd.Timestamp(scenarios[1].components[0].date).tz_localize("UTC")
    stale_weights = rebalance.stressed_backtest.effective_weights.loc[gap_date]
    assert abs(float(stale_weights["SPY"]) - 0.60) > experiment.numeric_tolerance
    stressed_asset_returns = rebalance.transform.stressed_asset_returns.loc[gap_date]
    target_return = float(
        0.60 * stressed_asset_returns["SPY"] + 0.40 * stressed_asset_returns["TLT"]
    )
    stale_return = float(rebalance.stressed_backtest.gross_portfolio_returns.loc[gap_date])
    assert target_return - stale_return >= 0.0025

    friction = run_stressed_backtest(
        dataset,
        strategy,
        scenarios[2],
        experiment.transaction_cost_bps,
        experiment.numeric_tolerance,
        experiment.failure_rules,
        experiment.seed,
    )
    window = slice(
        pd.Timestamp(scenarios[2].evaluation_start).tz_localize("UTC"),
        pd.Timestamp(scenarios[2].evaluation_end).tz_localize("UTC"),
    )
    incremental_cost = float(
        (
            friction.stressed_backtest.transaction_costs.loc[window]
            - friction.baseline_backtest.transaction_costs.loc[window]
        ).sum()
    )
    scenario_return = float(
        (1.0 + friction.stressed_backtest.portfolio_returns.loc[window]).prod() - 1.0
    )
    assert scenario_return < 0.0
    assert incremental_cost >= 0.005
    assert incremental_cost / abs(scenario_return) >= 0.10


def test_volatility_row_is_schema_valid_but_removed_without_sizing(
    tmp_path: Path,
    hypothesis_context: tuple[
        StoredDataset,
        FixedMonthly6040Strategy,
        ExperimentSpec,
        AttackPolicy,
        AttackValidationContext,
    ],
) -> None:
    """A numeric volatility failure cannot become a false volatility-sizing claim."""
    dataset, strategy, experiment, policy, _ = hypothesis_context
    dates = dataset.data.index
    scenario = StressScenario(
        scenario_id="unsupported-volatility-sizing",
        evaluation_start=dates[0].date(),
        evaluation_end=dates[-1].date(),
        components=(
            StressComponent(
                family="volatility_multiplier",
                start_date=dates[80].date(),
                end_date=dates[99].date(),
                symbols=("SPY", "TLT"),
                volatility_multiplier=3.0,
            ),
        ),
        hypothesis="Narrative falsely calling this a volatility-sizing strategy is untrusted.",
        headline="Numeric volatility stress only",
    )
    row = policy.hypothesis_for_scenario(scenario)
    assert row is not None
    assert row.hypothesis_family is AttackHypothesisFamily.VOLATILITY_REGIME_JUMP
    policy.validate_scenario(scenario)

    baseline = run_backtest(dataset, strategy, 250.0, experiment.numeric_tolerance)
    direct = evaluate_scenario(
        dataset=dataset,
        strategy=strategy,
        experiment=experiment,
        baseline=baseline,
        scenario=scenario,
        round_number=1,
        candidate_number=1,
        config_sha256=canonical_json_sha256(experiment),
    )
    assert direct.result.status is ResultStatus.VALID
    assert direct.result.breach_count >= 1

    runtime_policy = policy.for_strategy(strategy.spec)
    assert runtime_policy.hypothesis_for_scenario(scenario) is None
    assert AttackHypothesisFamily.VOLATILITY_REGIME_JUMP not in {
        item.hypothesis_family for item in runtime_policy.hypotheses
    }
    external_spec = StrategySpec(
        strategy_id="external-without-fixed-or-volatility-mechanism",
        kind="external_weights",
        symbols=("SPY", "TLT"),
        target_weights=None,
        rebalance_frequency="external",
    )
    external_families = {
        item.hypothesis_family for item in policy.for_strategy(external_spec).hypotheses
    }
    assert AttackHypothesisFamily.REBALANCE_TIMING_GAP not in external_families
    assert AttackHypothesisFamily.VOLATILITY_REGIME_JUMP not in external_families
    one_candidate_experiment = ExperimentSpec.model_validate(
        {
            **experiment.model_dump(mode="python"),
            "max_candidates_per_round": 1,
            "max_total_scenarios": 1,
            "top_k": 1,
        }
    )
    proposer = FakeScenarioProposer(
        responses=(
            AttackBatch(
                experiment_id=one_candidate_experiment.experiment_id,
                round_number=1,
                scenarios=(scenario,),
            ),
        )
    )
    run = AttackerService(proposer).run(
        dataset=dataset,
        strategy=strategy,
        experiment=one_candidate_experiment,
        policy=policy,
        artifact_directory=tmp_path / "unsupported-volatility",
    )
    assert len(proposer.calls) == 1
    assert AttackHypothesisFamily.VOLATILITY_REGIME_JUMP not in {
        item.hypothesis_family for item in proposer.calls[0].policy.hypotheses
    }
    assert run.evaluated_scenarios == 0
    assert run.rejected_scenarios == 1
    assert "active hypothesis template" in (run.proposals[0].rejection_detail or "")


def test_hypothesis_boundaries_and_invalid_assumptions_are_rejected(
    hypothesis_context: tuple[
        StoredDataset,
        FixedMonthly6040Strategy,
        ExperimentSpec,
        AttackPolicy,
        AttackValidationContext,
    ],
) -> None:
    """Inclusive endpoints pass; symbol, offset, window, cost, and turnover assumptions do not."""
    dataset, strategy, _, policy, context = hypothesis_context

    minimum_inflation = _inflation_scenario(
        dataset,
        window_rows=20,
        duration_rows=5,
        spy_shock=-0.25,
        tlt_shock=-0.20,
    )
    maximum_inflation = _inflation_scenario(
        dataset,
        window_start=1,
        window_rows=126,
        duration_rows=20,
        spy_shock=-0.05,
        tlt_shock=-0.04,
    )
    policy.validate_scenario(minimum_inflation, context=context)
    policy.validate_scenario(maximum_inflation, context=context)

    short_window = _inflation_scenario(dataset, window_rows=19)
    with pytest.raises(AttackPolicyViolation, match=r"20\.\.126"):
        policy.validate_scenario(short_window, context=context)
    symbol_specific = _inflation_scenario(dataset, tlt_shock=-0.21)
    with pytest.raises(AttackPolicyViolation, match="TLT shock"):
        policy.validate_scenario(symbol_specific, context=context)

    volatility_row = next(
        row
        for row in policy.hypotheses
        if row.hypothesis_family is AttackHypothesisFamily.VOLATILITY_REGIME_JUMP
    )
    assert volatility_row.lookback_rows.minimum == 20
    assert volatility_row.lookback_rows.maximum == 60
    assert volatility_row.stress_duration_rows.minimum == 5
    assert volatility_row.stress_duration_rows.maximum == 20
    assert volatility_row.volatility_multiplier.minimum == 1.50
    assert volatility_row.volatility_multiplier.maximum == 3.00
    dates = dataset.data.index
    invalid_multiplier = StressScenario(
        scenario_id="volatility-outside-bound",
        evaluation_start=dates[0].date(),
        evaluation_end=dates[-1].date(),
        components=(
            StressComponent(
                family="volatility_multiplier",
                start_date=dates[30].date(),
                end_date=dates[34].date(),
                symbols=("SPY", "TLT"),
                volatility_multiplier=1.49,
            ),
        ),
        hypothesis="Inert volatility boundary test.",
        headline="Volatility multiplier below the row boundary",
    )
    with pytest.raises(AttackPolicyViolation, match="volatility-regime multiplier"):
        policy.validate_scenario(invalid_multiplier)

    for offset in (-3, -2, -1):
        policy.validate_scenario(
            _rebalance_scenario(dataset, strategy, offset=offset),
            context=context,
        )
    with pytest.raises(AttackPolicyViolation, match="-3, -2, or -1"):
        policy.validate_scenario(
            _rebalance_scenario(dataset, strategy, offset=-4),
            context=context,
        )
    with pytest.raises(AttackPolicyViolation, match="TLT gap"):
        policy.validate_scenario(
            _rebalance_scenario(dataset, strategy, tlt_shock=-0.13),
            context=context,
        )

    policy.validate_scenario(_friction_scenario(dataset, 2.0), context=context)
    policy.validate_scenario(_friction_scenario(dataset, 5.0), context=context)
    zero_cost = AttackValidationContext(
        strategy_spec=context.strategy_spec,
        market_dates=context.market_dates,
        rebalance_dates=context.rebalance_dates,
        transaction_cost_bps=0.0,
        positive_turnover_dates=context.positive_turnover_dates,
    )
    with pytest.raises(AttackPolicyViolation, match="positive baseline"):
        policy.validate_scenario(_friction_scenario(dataset), context=zero_cost)
    no_turnover = AttackValidationContext(
        strategy_spec=context.strategy_spec,
        market_dates=context.market_dates,
        rebalance_dates=context.rebalance_dates,
        transaction_cost_bps=250.0,
        positive_turnover_dates=frozenset(),
    )
    with pytest.raises(AttackPolicyViolation, match="requires a trade"):
        policy.validate_scenario(_friction_scenario(dataset), context=no_turnover)


def test_hypothesis_ablation_identifies_each_constructed_fixture_failure(
    tmp_path: Path,
    hypothesis_context: tuple[
        StoredDataset,
        FixedMonthly6040Strategy,
        ExperimentSpec,
        AttackPolicy,
        AttackValidationContext,
    ],
) -> None:
    """Removing one structured row removes only that row's fixture discovery."""
    dataset, strategy, experiment, policy, _ = hypothesis_context
    scenarios = (
        _inflation_scenario(dataset),
        _rebalance_scenario(dataset, strategy),
        _friction_scenario(dataset),
    )
    expected_by_id = {
        "inflation-fixture": AttackHypothesisFamily.INFLATION_CORRELATION_BREAK,
        "rebalance-fixture": AttackHypothesisFamily.REBALANCE_TIMING_GAP,
        "friction-fixture": AttackHypothesisFamily.TRADING_FRICTION_BREAK,
    }

    for scenario_id, removed_family in expected_by_id.items():
        ablated = policy.model_copy(
            update={
                "hypotheses": tuple(
                    row
                    for row in policy.hypotheses
                    if row.hypothesis_family is not removed_family
                )
            }
        )
        run = run_attack(
            dataset=dataset,
            strategy=strategy,
            experiment=experiment,
            policy=ablated,
            proposer=_OneBatchProposer(scenarios),
            artifact_directory=tmp_path / f"ablate-{removed_family.value}",
        )
        records = {record.result.scenario_id: record for record in run.evaluations}
        assert records[scenario_id].result.status is ResultStatus.REJECTED
        assert "active hypothesis template" in (
            records[scenario_id].result.rejection_detail or ""
        )
        discovered = {
            expected_by_id[record.result.scenario_id]
            for record in run.evaluations
            if record.result.status is ResultStatus.VALID
            and record.result.breach_count >= 1
        }
        assert discovered == set(expected_by_id.values()) - {removed_family}


def test_attacker_prompt_keeps_narrative_non_authoritative() -> None:
    prompt = (ROOT / "prompts" / "attacker.md").read_text(encoding="utf-8")
    assert "only rows present in `AttackPolicy.hypotheses`" in prompt
    assert "inert, non-authoritative metadata" in prompt
    assert "calculated only by the\ndeterministic engine" in prompt
