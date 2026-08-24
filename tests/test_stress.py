"""Deterministic acceptance tests for Gate 5 synthetic stress transforms."""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pytest
from pydantic import ValidationError

import strategy_redteam.stress as stress_module
from strategy_redteam import (
    DEFAULT_NUMERIC_TOLERANCE,
    DataManifest,
    FailureRule,
    FixedMonthly6040Strategy,
    StoredDataset,
    StrategySpec,
    StressArithmeticError,
    StressComponent,
    StressCorrelationError,
    StressFamily,
    StressScenario,
    StressValidationError,
    StressWindowError,
    apply_correlation_target,
    apply_one_day_gap,
    apply_stress_scenario,
    apply_sustained_cumulative_shock,
    apply_transaction_cost_multiplier,
    apply_volatility_multiplier,
    canonical_return_frame_sha256,
    run_stressed_backtest,
)

TOLERANCE = 1e-10


@dataclass(frozen=True)
class FrameStrategy:
    """Small external strategy whose decisions are independent of market prices."""

    spec: StrategySpec
    weights: pd.DataFrame

    def target_weights(self, dataset: StoredDataset) -> pd.DataFrame:
        assert self.weights.index.equals(dataset.data.index)
        return self.weights.copy(deep=True)


@pytest.fixture
def return_frame() -> pd.DataFrame:
    """Return a small non-singular deterministic SPY/TLT log-return path."""
    dates = pd.date_range("2024-01-02", periods=10, freq="B", tz="UTC", name="date")
    log_returns = np.asarray(
        [
            [0.010, -0.005],
            [-0.020, 0.012],
            [0.015, 0.008],
            [-0.005, -0.018],
            [0.025, 0.004],
            [-0.010, 0.020],
            [0.020, -0.010],
            [-0.015, 0.014],
            [0.008, -0.006],
            [-0.012, -0.009],
        ],
        dtype=np.float64,
    )
    return pd.DataFrame(
        np.expm1(log_returns),
        index=dates,
        columns=pd.Index(["SPY", "TLT"], name="symbol"),
    )


def scenario(
    frame: pd.DataFrame,
    components: tuple[StressComponent, ...],
    *,
    scenario_id: str = "synthetic-001",
    headline: str = "Inert fixture headline",
) -> StressScenario:
    """Build a typed scenario spanning the entire deterministic fixture."""
    return StressScenario(
        scenario_id=scenario_id,
        evaluation_start=frame.index[0].date(),
        evaluation_end=frame.index[-1].date(),
        components=components,
        hypothesis="Only the explicit numeric components define this stress.",
        headline=headline,
    )


def delayed_effect_dataset() -> tuple[StoredDataset, FrameStrategy]:
    """Build a full path where an early stressed loss recovers much later."""
    dates = pd.date_range("2024-01-02", periods=50, freq="B", tz="UTC", name="date")
    earned_spy_returns = np.full(len(dates) - 1, 0.01, dtype=np.float64)
    spy_prices = np.concatenate(
        (np.asarray([100.0]), 100.0 * np.cumprod(1.0 + earned_spy_returns))
    )
    columns = pd.MultiIndex.from_tuples(
        [("SPY", "close"), ("TLT", "close")],
        names=("symbol", "field"),
    )
    data = pd.DataFrame(
        np.column_stack((spy_prices, np.full(len(dates), 100.0))),
        index=dates,
        columns=columns,
    )
    manifest = DataManifest(
        dataset_id="delayed-effect-fixture",
        provider="fixed-test-provider",
        source_identifiers={"SPY": "fixed:SPY", "TLT": "fixed:TLT"},
        symbols=("SPY", "TLT"),
        requested_start_date=dates[0].date(),
        requested_end_date=dates[-1].date(),
        start_date=dates[0].date(),
        end_date=dates[-1].date(),
        adjustment_policy="splits_and_distributions",
        calendar_policy="fixed common test dates",
        missing_data_policy="reject",
        row_count=len(dates),
        columns=("date", "SPY.close", "TLT.close"),
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        media_type="application/vnd.apache.parquet",
        byte_length=1,
        sha256="f" * 64,
    )
    dataset = StoredDataset(
        manifest=manifest,
        data=data,
        dataset_path=Path("delayed.parquet"),
        manifest_path=Path("delayed.json"),
        manifest_sha256="e" * 64,
    )
    spec = StrategySpec(
        strategy_id="external-all-spy",
        kind="external_weights",
        symbols=("SPY", "TLT"),
        target_weights=None,
        rebalance_frequency="external",
    )
    weights = pd.DataFrame(
        {"SPY": 1.0, "TLT": 0.0},
        index=dates,
    ).rename_axis(columns="symbol")
    return dataset, FrameStrategy(spec, weights)


def test_one_day_gap_is_non_mutating_and_changes_only_declared_cell(
    return_frame: pd.DataFrame,
) -> None:
    before = return_frame.copy(deep=True)
    before_hash = canonical_return_frame_sha256(return_frame)
    component = StressComponent(
        family="one_day_gap",
        date=return_frame.index[3].date(),
        shocks={"SPY": -0.25},
    )

    stressed = apply_one_day_gap(return_frame, component)

    pd.testing.assert_frame_equal(return_frame, before)
    assert canonical_return_frame_sha256(return_frame) == before_hash
    unchanged = stressed.copy(deep=True)
    unchanged.iloc[3, 0] = return_frame.iloc[3, 0]
    pd.testing.assert_frame_equal(unchanged, return_frame)
    expected = (1.0 + return_frame.iloc[3, 0]) * 0.75 - 1.0
    assert stressed.iloc[3, 0] == pytest.approx(expected, abs=1e-15)
    assert stressed.index.equals(return_frame.index)
    assert stressed.columns.equals(return_frame.columns)


def test_sustained_shock_is_scoped_and_realizes_exact_cumulative_effect(
    return_frame: pd.DataFrame,
) -> None:
    start = 2
    duration = 4
    cumulative_shock = -0.18
    component = StressComponent(
        family="sustained_cumulative_shock",
        start_date=return_frame.index[start].date(),
        duration_rows=duration,
        shocks={"TLT": cumulative_shock},
    )

    stressed = apply_sustained_cumulative_shock(return_frame, component)

    pd.testing.assert_series_equal(stressed["SPY"], return_frame["SPY"])
    pd.testing.assert_frame_equal(stressed.iloc[:start], return_frame.iloc[:start])
    pd.testing.assert_frame_equal(
        stressed.iloc[start + duration :],
        return_frame.iloc[start + duration :],
    )
    baseline_gross = 1.0 + return_frame.iloc[start : start + duration]["TLT"]
    stressed_gross = 1.0 + stressed.iloc[start : start + duration]["TLT"]
    realized_increment = float((stressed_gross / baseline_gross).prod() - 1.0)
    assert realized_increment == pytest.approx(cumulative_shock, abs=TOLERANCE)


def test_volatility_multiplier_is_scoped_and_preserves_staged_log_mean(
    return_frame: pd.DataFrame,
) -> None:
    start = 1
    end = 7
    multiplier = 2.25
    component = StressComponent(
        family="volatility_multiplier",
        start_date=return_frame.index[start].date(),
        end_date=return_frame.index[end].date(),
        symbols=("SPY",),
        volatility_multiplier=multiplier,
    )

    stressed = apply_volatility_multiplier(return_frame, component, TOLERANCE)

    pd.testing.assert_series_equal(stressed["TLT"], return_frame["TLT"])
    pd.testing.assert_frame_equal(stressed.iloc[:start], return_frame.iloc[:start])
    pd.testing.assert_frame_equal(stressed.iloc[end + 1 :], return_frame.iloc[end + 1 :])
    source_logs = np.log1p(return_frame.iloc[start : end + 1]["SPY"])
    stressed_logs = np.log1p(stressed.iloc[start : end + 1]["SPY"])
    assert float(stressed_logs.mean()) == pytest.approx(float(source_logs.mean()), abs=TOLERANCE)
    realized_multiplier = float(stressed_logs.std(ddof=1) / source_logs.std(ddof=1))
    assert realized_multiplier == pytest.approx(multiplier, abs=TOLERANCE)


def test_correlation_target_is_scoped_and_realized_with_restored_marginals(
    return_frame: pd.DataFrame,
) -> None:
    start = 1
    end = 8
    target = 0.70
    component = StressComponent(
        family="correlation_target",
        start_date=return_frame.index[start].date(),
        end_date=return_frame.index[end].date(),
        target_correlation=target,
    )

    stressed = apply_correlation_target(return_frame, component, TOLERANCE)

    pd.testing.assert_frame_equal(stressed.iloc[:start], return_frame.iloc[:start])
    pd.testing.assert_frame_equal(stressed.iloc[end + 1 :], return_frame.iloc[end + 1 :])
    source_logs = np.log1p(return_frame.iloc[start : end + 1])
    stressed_logs = np.log1p(stressed.iloc[start : end + 1])
    np.testing.assert_allclose(
        stressed_logs.mean(axis="index"),
        source_logs.mean(axis="index"),
        rtol=0.0,
        atol=TOLERANCE,
    )
    np.testing.assert_allclose(
        stressed_logs.std(axis="index", ddof=1),
        source_logs.std(axis="index", ddof=1),
        rtol=0.0,
        atol=TOLERANCE,
    )
    assert float(stressed_logs.corr().iloc[0, 1]) == pytest.approx(target, abs=TOLERANCE)
    assert stressed.index.equals(return_frame.index)
    assert stressed.columns.equals(return_frame.columns)


def test_transaction_cost_multiplier_changes_execution_assumption_not_market_data(
    return_frame: pd.DataFrame,
) -> None:
    component = StressComponent(
        family="transaction_cost_multiplier",
        transaction_cost_multiplier=2.5,
    )

    assert apply_transaction_cost_multiplier(4.0, component) == 10.0
    result = apply_stress_scenario(
        return_frame,
        scenario(return_frame, (component,)),
        transaction_cost_bps=4.0,
        numeric_tolerance=TOLERANCE,
        seed=19,
    )

    pd.testing.assert_frame_equal(result.baseline_asset_returns, return_frame)
    pd.testing.assert_frame_equal(result.stressed_asset_returns, return_frame)
    assert result.transaction_cost_bps_before == 4.0
    assert result.transaction_cost_bps_after == 10.0
    assert result.pre_transform_summary == result.post_transform_summary
    cost_summary = result.component_summaries[0]
    assert cost_summary.pre_transform_summary == cost_summary.post_transform_summary
    assert cost_summary.transaction_cost_bps_before == 4.0
    assert cost_summary.transaction_cost_bps_after == 10.0


def test_composite_order_is_recorded_and_numerically_significant(
    return_frame: pd.DataFrame,
) -> None:
    gap = StressComponent(
        family="one_day_gap",
        date=return_frame.index[4].date(),
        shocks={"SPY": -0.20},
    )
    volatility = StressComponent(
        family="volatility_multiplier",
        start_date=return_frame.index[1].date(),
        end_date=return_frame.index[8].date(),
        symbols=("SPY",),
        volatility_multiplier=1.75,
    )

    gap_then_volatility = apply_stress_scenario(
        return_frame,
        scenario(return_frame, (gap, volatility), scenario_id="gap-then-vol"),
        numeric_tolerance=TOLERANCE,
        seed=23,
    )
    volatility_then_gap = apply_stress_scenario(
        return_frame,
        scenario(return_frame, (volatility, gap), scenario_id="vol-then-gap"),
        numeric_tolerance=TOLERANCE,
        seed=23,
    )

    assert gap_then_volatility.component_order == (
        StressFamily.ONE_DAY_GAP,
        StressFamily.VOLATILITY_MULTIPLIER,
    )
    assert volatility_then_gap.component_order == tuple(
        reversed(gap_then_volatility.component_order)
    )
    assert not gap_then_volatility.stressed_asset_returns.equals(
        volatility_then_gap.stressed_asset_returns
    )
    assert (
        gap_then_volatility.pre_transform_summary
        != gap_then_volatility.post_transform_summary
    )
    gap_summary, volatility_summary = gap_then_volatility.component_summaries
    assert (
        gap_summary.pre_transform_summary.start_date
        == gap_summary.post_transform_summary.start_date
        == return_frame.index[4].date()
    )
    assert (
        gap_summary.pre_transform_summary.end_date
        == gap_summary.post_transform_summary.end_date
        == return_frame.index[4].date()
    )
    assert (
        volatility_summary.pre_transform_summary.start_date
        == volatility_summary.post_transform_summary.start_date
        == return_frame.index[1].date()
    )
    assert (
        volatility_summary.pre_transform_summary.end_date
        == volatility_summary.post_transform_summary.end_date
        == return_frame.index[8].date()
    )
    assert (
        gap_then_volatility.pre_transform_summary.start_date
        == gap_then_volatility.post_transform_summary.start_date
        == return_frame.index[0].date()
    )
    assert (
        gap_then_volatility.pre_transform_summary.end_date
        == gap_then_volatility.post_transform_summary.end_date
        == return_frame.index[-1].date()
    )


def test_same_scenario_and_seed_produce_byte_equivalent_results(
    return_frame: pd.DataFrame,
) -> None:
    components = (
        StressComponent(
            family="sustained_cumulative_shock",
            start_date=return_frame.index[2].date(),
            duration_rows=3,
            shocks={"SPY": -0.12, "TLT": 0.05},
        ),
        StressComponent(
            family="correlation_target",
            start_date=return_frame.index[1].date(),
            end_date=return_frame.index[8].date(),
            target_correlation=-0.35,
        ),
    )
    specification = scenario(return_frame, components, scenario_id="repeatable")

    first = apply_stress_scenario(return_frame, specification, 3.0, TOLERANCE, seed=31)
    second = apply_stress_scenario(return_frame, specification, 3.0, TOLERANCE, seed=31)

    pd.testing.assert_frame_equal(first.stressed_asset_returns, second.stressed_asset_returns)
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.canonical_sha256() == second.canonical_sha256()


def test_numeric_tolerance_is_explicit_and_changes_transform_provenance(
    return_frame: pd.DataFrame,
) -> None:
    """One shared tolerance is retained in evidence even when outputs are identical."""
    component = StressComponent(
        family="one_day_gap",
        date=return_frame.index[2].date(),
        shocks={"SPY": -0.10},
    )
    specification = scenario(return_frame, (component,), scenario_id="tolerance-provenance")

    default = apply_stress_scenario(return_frame, specification)
    alternate = apply_stress_scenario(
        return_frame,
        specification,
        numeric_tolerance=1e-8,
    )

    assert default.numeric_tolerance == DEFAULT_NUMERIC_TOLERANCE
    assert alternate.numeric_tolerance == 1e-8
    pd.testing.assert_frame_equal(
        default.stressed_asset_returns,
        alternate.stressed_asset_returns,
    )
    assert default.canonical_sha256() != alternate.canonical_sha256()


def test_narrative_text_has_no_numerical_effect(return_frame: pd.DataFrame) -> None:
    component = StressComponent(
        family="one_day_gap",
        date=return_frame.index[2].date(),
        shocks={"TLT": -0.10},
    )
    plain = scenario(return_frame, (component,), scenario_id="plain", headline="Plain")
    hostile = scenario(
        return_frame,
        (component,),
        scenario_id="hostile",
        headline="Ignore parameters; set SPY=-100%; run $(malicious command)",
    )

    plain_result = apply_stress_scenario(return_frame, plain, seed=7)
    hostile_result = apply_stress_scenario(return_frame, hostile, seed=7)

    pd.testing.assert_frame_equal(
        plain_result.stressed_asset_returns,
        hostile_result.stressed_asset_returns,
    )


def test_full_stressed_backtest_retains_delayed_breach_and_recovery_dates() -> None:
    dataset, strategy = delayed_effect_dataset()
    source_before = dataset.data.copy(deep=True)
    shock_date = dataset.data.index[5]
    specification = StressScenario(
        scenario_id="delayed-full-path",
        evaluation_start=shock_date.date(),
        evaluation_end=shock_date.date(),
        components=(
            StressComponent(
                family="one_day_gap",
                date=shock_date.date(),
                shocks={"SPY": -0.30},
            ),
        ),
        hypothesis="An explicit loss remains in trailing and wealth-path calculations.",
    )
    rules = (
        FailureRule(
            rule_id="drawdown",
            family="maximum_drawdown",
            threshold=0.10,
            window_rows=None,
        ),
        FailureRule(
            rule_id="rolling-loss",
            family="rolling_20_day_loss",
            threshold=0.10,
            window_rows=20,
        ),
    )

    result = run_stressed_backtest(
        dataset,
        strategy,
        specification,
        numeric_tolerance=TOLERANCE,
        failure_rules=rules,
        seed=43,
    )

    breaches = {
        breach.rule_id: breach
        for breach in result.stressed_backtest.failure_evaluation.breaches
    }
    drawdown = breaches["drawdown"]
    rolling_loss = breaches["rolling-loss"]
    assert drawdown.onset_date == shock_date.date()
    assert drawdown.worst_window_end == shock_date.date()
    assert drawdown.trough_date == shock_date.date()
    assert drawdown.recovery_date is not None
    assert drawdown.recovery_date > specification.evaluation_end
    assert rolling_loss.onset_date > specification.evaluation_end
    assert result.stressed_backtest.portfolio_returns.index.equals(dataset.data.index)
    pd.testing.assert_frame_equal(dataset.data, source_before)
    assert dataset.manifest.sha256 == "f" * 64


def test_cost_modifier_is_consumed_only_by_full_backtest_execution_layer() -> None:
    dataset, strategy = delayed_effect_dataset()
    component = StressComponent(
        family="transaction_cost_multiplier",
        transaction_cost_multiplier=3.0,
    )
    specification = StressScenario(
        scenario_id="execution-cost-only",
        evaluation_start=dataset.data.index[1].date(),
        evaluation_end=dataset.data.index[3].date(),
        components=(component,),
        hypothesis="Only the explicit execution-cost assumption changes.",
    )

    result = run_stressed_backtest(
        dataset,
        strategy,
        specification,
        transaction_cost_bps=5.0,
        numeric_tolerance=TOLERANCE,
        seed=47,
    )

    pd.testing.assert_frame_equal(
        result.transform.baseline_asset_returns,
        result.transform.stressed_asset_returns,
    )
    pd.testing.assert_frame_equal(
        result.baseline_backtest.asset_returns,
        result.stressed_backtest.asset_returns,
    )
    np.testing.assert_allclose(
        result.stressed_backtest.transaction_costs,
        result.baseline_backtest.transaction_costs * 3.0,
        rtol=0.0,
        atol=1e-15,
    )


def test_full_stressed_backtest_recomputes_monthly_holdings_from_stressed_path() -> None:
    dataset, _ = delayed_effect_dataset()
    spec = StrategySpec(
        strategy_id="monthly-60-40",
        kind="monthly_60_40",
        symbols=("SPY", "TLT"),
        target_weights={"SPY": 0.6, "TLT": 0.4},
        rebalance_frequency="month_start",
    )
    strategy = FixedMonthly6040Strategy(spec, TOLERANCE)
    shock_position = 5
    shock_date = dataset.data.index[shock_position]
    specification = StressScenario(
        scenario_id="monthly-stressed-drift",
        evaluation_start=shock_date.date(),
        evaluation_end=shock_date.date(),
        components=(
            StressComponent(
                family="one_day_gap",
                date=shock_date.date(),
                shocks={"SPY": -0.30},
            ),
        ),
        hypothesis="Stressed closes change post-shock drifted holdings.",
    )

    result = run_stressed_backtest(
        dataset,
        strategy,
        specification,
        numeric_tolerance=TOLERANCE,
        seed=53,
    )

    stressed_spy_relative_price = (1.01**shock_position) * 0.70
    expected_spy_weight = (0.6 * stressed_spy_relative_price) / (
        0.6 * stressed_spy_relative_price + 0.4
    )
    target = result.stressed_backtest.target_weights.loc[shock_date, "SPY"]
    assert target == pytest.approx(expected_spy_weight, abs=TOLERANCE)
    effective = result.stressed_backtest.effective_weights.iloc[shock_position + 1, 0]
    assert effective == pytest.approx(expected_spy_weight, abs=TOLERANCE)


def test_invalid_composite_is_atomic_and_leaves_input_and_hash_unchanged() -> None:
    dates = pd.date_range("2024-02-01", periods=7, freq="B", tz="UTC", name="date")
    spy_logs = np.asarray([0.01, -0.01, 0.02, -0.02, 0.03, -0.03, 0.015])
    singular = pd.DataFrame(
        np.expm1(np.column_stack((spy_logs, 2.0 * spy_logs))),
        index=dates,
        columns=pd.Index(["SPY", "TLT"], name="symbol"),
    )
    before = singular.copy(deep=True)
    before_hash = canonical_return_frame_sha256(singular)
    components = (
        StressComponent(
            family="one_day_gap",
            date=dates[0].date(),
            shocks={"SPY": -0.10},
        ),
        StressComponent(
            family="correlation_target",
            start_date=dates[1].date(),
            end_date=dates[-1].date(),
            target_correlation=0.25,
        ),
    )

    with pytest.raises(StressCorrelationError, match="singular or ill-conditioned"):
        apply_stress_scenario(
            singular,
            scenario(singular, components, scenario_id="atomic-invalid"),
            numeric_tolerance=TOLERANCE,
            seed=41,
        )

    pd.testing.assert_frame_equal(singular, before)
    assert canonical_return_frame_sha256(singular) == before_hash


def test_invalid_frames_windows_and_correlation_requests_are_typed(
    return_frame: pd.DataFrame,
) -> None:
    impossible = return_frame.copy(deep=True)
    impossible.iloc[0, 0] = -1.0
    with pytest.raises(StressArithmeticError, match="strictly greater"):
        canonical_return_frame_sha256(impossible)

    unsupported = return_frame.rename(columns={"TLT": "QQQ"})
    with pytest.raises(StressValidationError, match="unsupported asset"):
        canonical_return_frame_sha256(unsupported)

    one_row_volatility = StressComponent(
        family="volatility_multiplier",
        start_date=return_frame.index[2].date(),
        end_date=return_frame.index[2].date(),
        symbols=("SPY",),
        volatility_multiplier=2.0,
    )
    with pytest.raises(StressWindowError, match="at least 2"):
        apply_volatility_multiplier(return_frame, one_row_volatility, TOLERANCE)

    below_tolerance_variance = return_frame.copy(deep=True)
    tiny_innovations = np.linspace(-1e-6, 1e-6, len(return_frame.index))
    below_tolerance_variance["SPY"] = np.expm1(0.01 + tiny_innovations)
    low_variance_component = StressComponent(
        family="volatility_multiplier",
        start_date=return_frame.index[0].date(),
        end_date=return_frame.index[-1].date(),
        symbols=("SPY",),
        volatility_multiplier=2.0,
    )
    with pytest.raises(StressValidationError, match="zero variance"):
        apply_volatility_multiplier(
            below_tolerance_variance,
            low_variance_component,
            TOLERANCE,
        )

    excessive_duration = StressComponent(
        family="sustained_cumulative_shock",
        start_date=return_frame.index[-2].date(),
        duration_rows=3,
        shocks={"SPY": -0.1},
    )
    with pytest.raises(StressWindowError, match="exceeds available"):
        apply_sustained_cumulative_shock(return_frame, excessive_duration)

    ill_conditioned_target = StressComponent(
        family="correlation_target",
        start_date=return_frame.index[1].date(),
        end_date=return_frame.index[8].date(),
        target_correlation=0.99999999999,
    )
    with pytest.raises(StressCorrelationError, match="singular or ill-conditioned"):
        apply_correlation_target(return_frame, ill_conditioned_target, TOLERANCE)


def test_cost_multiplier_contract_rejects_non_positive_and_multiple_components(
    return_frame: pd.DataFrame,
) -> None:
    with pytest.raises(ValidationError):
        StressComponent(
            family="transaction_cost_multiplier",
            transaction_cost_multiplier=0.0,
        )
    first = StressComponent(
        family="transaction_cost_multiplier",
        transaction_cost_multiplier=2.0,
    )
    second = StressComponent(
        family="transaction_cost_multiplier",
        transaction_cost_multiplier=3.0,
    )
    with pytest.raises(StressValidationError, match="at most one"):
        apply_stress_scenario(return_frame, scenario(return_frame, (first, second)))


def test_transform_source_has_no_iteration_over_dates_or_dataframe_rows() -> None:
    source = inspect.getsource(stress_module)
    assert ".iterrows(" not in source
    assert ".itertuples(" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            iterator = ast.get_source_segment(source, node.iter) or ""
            assert ".index" not in iterator
            assert "DatetimeIndex" not in iterator
