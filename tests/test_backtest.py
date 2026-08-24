"""Deterministic acceptance tests for the Gate 3 strategy and baseline engine."""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import strategy_redteam.backtest as backtest_module
import strategy_redteam.strategy as strategy_module
from strategy_redteam import (
    AdjustmentPolicy,
    CSVWeightsError,
    CSVWeightsStrategy,
    DataManifest,
    ExperimentSpec,
    FailureRule,
    FixedMonthly6040Strategy,
    LocalDatasetStore,
    ProviderResult,
    StoredDataset,
    StrategySpec,
    Symbol,
    run_backtest,
)
from strategy_redteam.cli import app
from strategy_redteam.data import DATA_FIELDS, canonicalize_provider_data

DATA_HASH = "d" * 64


def make_dataset(
    spy: list[float],
    tlt: list[float],
    *,
    dates: pd.DatetimeIndex | None = None,
) -> StoredDataset:
    """Create a small manifest-labelled in-memory test dataset."""
    if dates is None:
        dates = pd.date_range("2024-01-02", periods=len(spy), freq="B", tz="UTC", name="date")
    columns = pd.MultiIndex.from_tuples(
        [("SPY", "close"), ("TLT", "close")],
        names=("symbol", "field"),
    )
    data = pd.DataFrame(np.column_stack((spy, tlt)), index=dates, columns=columns)
    manifest = DataManifest(
        dataset_id="tiny-dataset",
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
        sha256=DATA_HASH,
    )
    return StoredDataset(
        manifest=manifest,
        data=data,
        dataset_path=Path("tiny.parquet"),
        manifest_path=Path("tiny.json"),
        manifest_sha256="e" * 64,
    )


def monthly_spec() -> StrategySpec:
    return StrategySpec(
        strategy_id="monthly-60-40",
        kind="monthly_60_40",
        symbols=("SPY", "TLT"),
        target_weights={"SPY": 0.6, "TLT": 0.4},
        rebalance_frequency="month_start",
    )


def external_spec(**updates: object) -> StrategySpec:
    values: dict[str, object] = {
        "strategy_id": "external-daily",
        "kind": "external_weights",
        "symbols": ("SPY", "TLT"),
        "target_weights": None,
        "rebalance_frequency": "external",
    }
    values.update(updates)
    return StrategySpec.model_validate(values)


@dataclass(frozen=True)
class FrameStrategy:
    spec: StrategySpec
    weights: pd.DataFrame

    def target_weights(self, dataset: StoredDataset) -> pd.DataFrame:
        assert self.weights.index.equals(dataset.data.index)
        return self.weights.copy()


def test_hand_calculated_returns_turnover_costs_and_contributions() -> None:
    """A three-close buy-and-hold path reproduces exact arithmetic."""
    dataset = make_dataset([100.0, 110.0, 121.0], [100.0, 100.0, 100.0])
    strategy = FixedMonthly6040Strategy(monthly_spec(), 1e-12)

    result = run_backtest(dataset, strategy, 10.0, 1e-12)

    np.testing.assert_allclose(
        result.effective_weights.to_numpy(),
        np.array([[0.0, 0.0], [0.6, 0.4], [33.0 / 53.0, 20.0 / 53.0]]),
        rtol=0.0,
        atol=1e-15,
    )
    np.testing.assert_allclose(
        result.asset_contributions.to_numpy(),
        np.array([[0.0, 0.0], [0.06, 0.0], [(33.0 / 53.0) * 0.1, 0.0]]),
        rtol=0.0,
        atol=1e-15,
    )
    np.testing.assert_allclose(result.turnover, [0.0, 1.0, 0.0], rtol=0.0, atol=1e-15)
    np.testing.assert_allclose(
        result.transaction_costs,
        [0.0, 0.001, 0.0],
        rtol=0.0,
        atol=1e-15,
    )
    np.testing.assert_allclose(
        result.portfolio_returns,
        [0.0, 0.059, (33.0 / 53.0) * 0.1],
        rtol=0.0,
        atol=1e-15,
    )
    assert result.metrics.observation_count == 2
    assert result.equity_curve.iloc[0] == 1.0


def test_future_spike_cannot_change_preceding_position_or_return() -> None:
    """A sentinel future close affects only its own observed return."""
    dates = pd.date_range("2024-01-02", periods=4, freq="B", tz="UTC", name="date")
    ordinary = make_dataset([100.0, 101.0, 102.0, 103.0], [100.0] * 4, dates=dates)
    sentinel = make_dataset([100.0, 101.0, 102.0, 10_300.0], [100.0] * 4, dates=dates)
    ordinary_strategy = FixedMonthly6040Strategy(monthly_spec(), 1e-12)
    sentinel_strategy = FixedMonthly6040Strategy(monthly_spec(), 1e-12)

    ordinary_result = run_backtest(ordinary, ordinary_strategy, 0.0, 1e-12)
    sentinel_result = run_backtest(sentinel, sentinel_strategy, 0.0, 1e-12)

    pd.testing.assert_frame_equal(
        ordinary_result.target_weights.iloc[:-1],
        sentinel_result.target_weights.iloc[:-1],
    )
    pd.testing.assert_frame_equal(
        ordinary_result.effective_weights,
        sentinel_result.effective_weights,
    )
    pd.testing.assert_series_equal(
        ordinary_result.portfolio_returns.iloc[:-1],
        sentinel_result.portfolio_returns.iloc[:-1],
    )


def test_monthly_rebalance_dates_use_first_observed_close() -> None:
    dates = pd.DatetimeIndex(
        pd.to_datetime(
            ["2024-01-29", "2024-01-31", "2024-02-01", "2024-02-28", "2024-03-04"],
            utc=True,
        ),
        name="date",
    )
    dataset = make_dataset([100.0] * 5, [100.0] * 5, dates=dates)
    strategy = FixedMonthly6040Strategy(monthly_spec(), 1e-12)

    assert strategy.rebalance_dates(dataset).tolist() == [
        dates[0],
        dates[2],
        dates[4],
    ]


def test_monthly_strategy_is_prefix_invariant_and_does_not_mark_truncated_end() -> None:
    """Future rows cannot alter prior decisions, weights, returns, turnover, or costs."""
    dates = pd.DatetimeIndex(
        pd.to_datetime(
            [
                "2024-01-29",
                "2024-01-31",
                "2024-02-01",
                "2024-02-02",
                "2024-02-15",
                "2024-03-01",
                "2024-03-04",
            ],
            utc=True,
        ),
        name="date",
    )
    spy = [100.0, 102.0, 101.0, 104.0, 103.0, 105.0, 106.0]
    tlt = [100.0, 99.0, 101.0, 100.0, 102.0, 101.0, 103.0]
    prefix_length = 5
    prefix = make_dataset(
        spy[:prefix_length],
        tlt[:prefix_length],
        dates=dates[:prefix_length],
    )
    extended = make_dataset(spy, tlt, dates=dates)
    prefix_strategy = FixedMonthly6040Strategy(monthly_spec(), 1e-12)
    extended_strategy = FixedMonthly6040Strategy(monthly_spec(), 1e-12)

    prefix_mask = prefix_strategy.rebalance_mask(prefix)
    extended_mask = extended_strategy.rebalance_mask(extended).iloc[:prefix_length]
    pd.testing.assert_series_equal(prefix_mask, extended_mask)
    assert not bool(prefix_mask.iloc[-1])
    assert prefix_strategy.rebalance_dates(prefix).tolist() == [dates[0], dates[2]]

    prefix_result = run_backtest(prefix, prefix_strategy, 7.0, 1e-12)
    extended_result = run_backtest(extended, extended_strategy, 7.0, 1e-12)
    pd.testing.assert_frame_equal(
        prefix_result.target_weights,
        extended_result.target_weights.iloc[:prefix_length],
    )
    pd.testing.assert_frame_equal(
        prefix_result.effective_weights,
        extended_result.effective_weights.iloc[:prefix_length],
    )
    pd.testing.assert_series_equal(
        prefix_result.portfolio_returns,
        extended_result.portfolio_returns.iloc[:prefix_length],
    )
    pd.testing.assert_series_equal(
        prefix_result.turnover,
        extended_result.turnover.iloc[:prefix_length],
    )
    pd.testing.assert_series_equal(
        prefix_result.transaction_costs,
        extended_result.transaction_costs.iloc[:prefix_length],
    )


def write_weights(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            ["date,SPY,TLT,QQQ", "2024-01-02,0.6,0.4,0", "2024-01-03,0.6,0.4,0"],
            "unknown symbols",
        ),
        (
            ["date,SPY,TLT", "2024-01-02,0.6,0.4", "2024-01-02,0.6,0.4"],
            "duplicate dates",
        ),
        (
            ["date,SPY,TLT", "2024-01-02,0.5,0.4", "2024-01-03,0.6,0.4"],
            "sum to one",
        ),
        (
            ["date,SPY,TLT", "2024-01-02,1.1,0", "2024-01-03,0.6,0.4"],
            "leverage is forbidden",
        ),
        (
            ["date,SPY,TLT", "2024-01-02,-0.1,1.1", "2024-01-03,0.6,0.4"],
            "short exposure is forbidden",
        ),
        (
            ["date,SPY,TLT", "2024-01-02,0.6,", "2024-01-03,0.6,0.4"],
            "missing weights",
        ),
    ],
)
def test_invalid_csv_weights_fail_clearly(
    tmp_path: Path,
    rows: list[str],
    message: str,
) -> None:
    dates = pd.date_range("2024-01-02", periods=2, freq="B", tz="UTC", name="date")
    dataset = make_dataset([100.0, 101.0], [100.0, 100.0], dates=dates)
    csv_path = write_weights(tmp_path / "weights.csv", rows)

    with pytest.raises(CSVWeightsError, match=message):
        CSVWeightsStrategy(external_spec(), csv_path, 1e-9).target_weights(dataset)


def test_strategy_spec_can_explicitly_allow_relaxed_external_weights(tmp_path: Path) -> None:
    dates = pd.date_range("2024-01-02", periods=2, freq="B", tz="UTC", name="date")
    dataset = make_dataset([100.0, 101.0], [100.0, 100.0], dates=dates)
    csv_path = write_weights(
        tmp_path / "relaxed.csv",
        ["date,SPY,TLT", "2024-01-02,-0.1,1.1"],
    )
    spec = external_spec(
        allow_short_exposure=True,
        allow_leverage=True,
        allow_missing_weights=True,
    )

    weights = CSVWeightsStrategy(spec, csv_path, 1e-9).target_weights(dataset)

    np.testing.assert_allclose(weights.iloc[0], [-0.1, 1.1])
    np.testing.assert_allclose(weights.iloc[1], [0.0, 0.0])


def test_monthly_strategy_rejects_relaxed_weight_permissions() -> None:
    with pytest.raises(ValidationError, match="does not permit relaxed"):
        StrategySpec(
            strategy_id="invalid-monthly",
            kind="monthly_60_40",
            symbols=("SPY", "TLT"),
            target_weights={"SPY": 0.6, "TLT": 0.4},
            rebalance_frequency="month_start",
            allow_leverage=True,
        )


def test_metrics_and_all_breach_dates_are_exact_and_repeatable() -> None:
    earned_returns = np.array([0.0] * 19 + [-0.2, 0.25, -0.3], dtype=np.float64)
    spy_prices = np.concatenate(([100.0], 100.0 * np.cumprod(1.0 + earned_returns)))
    dataset = make_dataset(spy_prices.tolist(), [100.0] * len(spy_prices))
    weights = pd.DataFrame(
        {"SPY": 1.0, "TLT": 0.0},
        index=dataset.data.index,
    ).rename_axis(columns="symbol")
    strategy = FrameStrategy(external_spec(), weights)
    rules = (
        FailureRule(
            rule_id="drawdown",
            family="maximum_drawdown",
            threshold=0.1,
            window_rows=None,
        ),
        FailureRule(
            rule_id="rolling-loss",
            family="rolling_20_day_loss",
            threshold=0.1,
            window_rows=20,
        ),
        FailureRule(
            rule_id="volatility",
            family="realized_volatility_multiple",
            threshold=0.9,
            window_rows=20,
        ),
    )

    first = run_backtest(dataset, strategy, 0.0, 1e-12, rules)
    second = run_backtest(dataset, strategy, 0.0, 1e-12, rules)

    assert first.metrics == second.metrics
    assert first.failure_evaluation == second.failure_evaluation
    assert first.metrics.total_return == pytest.approx(-0.3, abs=1e-15)
    assert first.metrics.maximum_drawdown == pytest.approx(0.3, abs=1e-15)
    assert first.metrics.worst_rolling_20_day_return == pytest.approx(-0.3, abs=1e-15)
    assert first.metrics.observation_count == 22
    drawdown, rolling_loss, volatility = first.failure_evaluation.breaches
    dates = dataset.data.index
    assert (drawdown.onset_date, drawdown.worst_window_start, drawdown.worst_window_end) == (
        dates[20].date(),
        dates[21].date(),
        dates[22].date(),
    )
    assert (
        rolling_loss.onset_date,
        rolling_loss.worst_window_start,
        rolling_loss.worst_window_end,
    ) == (dates[20].date(), dates[3].date(), dates[22].date())
    assert (volatility.onset_date, volatility.worst_window_start, volatility.worst_window_end) == (
        dates[20].date(),
        dates[1].date(),
        dates[20].date(),
    )
    disclosure = first.failure_evaluation.non_evaluable_windows[0]
    assert disclosure.rule_id == "volatility"
    assert disclosure.window_end_dates == tuple(dates[1:20].strftime("%Y-%m-%d").tolist())


def test_engine_source_has_no_python_iteration_over_rows_or_timestamps() -> None:
    """Gate 3 may iterate bounded rules/symbols, never DataFrame dates or rows."""
    for module in (strategy_module, backtest_module):
        source = inspect.getsource(module)
        assert ".iterrows(" not in source
        assert ".itertuples(" not in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                iterator = ast.get_source_segment(source, node.iter) or ""
                assert ".index" not in iterator
                assert "DatetimeIndex" not in iterator


def canonical_store_dataset(root: Path) -> StoredDataset:
    dates = pd.date_range("2024-01-02", periods=22, freq="B", tz="UTC", name="date")
    columns = pd.MultiIndex.from_product(
        (["SPY", "TLT"], DATA_FIELDS),
        names=("symbol", "field"),
    )
    values = np.empty((len(dates), len(columns)), dtype=np.float64)
    values[:, :4] = 100.0
    values[:, 4] = 1_000.0
    values[:, 5:9] = 100.0
    values[:, 9] = 1_000.0
    raw = pd.DataFrame(values, index=dates, columns=columns)
    canonical = canonicalize_provider_data(
        raw,
        (Symbol.SPY, Symbol.TLT),
        dates[0].date(),
        dates[-1].date(),
    )
    provider_result = ProviderResult(
        data=canonical,
        source_identifiers={Symbol.SPY: "fixed:SPY", Symbol.TLT: "fixed:TLT"},
    )
    return LocalDatasetStore(root).put(
        provider_result.data,
        "fixed-test-provider",
        provider_result.source_identifiers,
        (Symbol.SPY, Symbol.TLT),
        dates[0].date(),
        dates[-1].date(),
        AdjustmentPolicy.SPLITS_AND_DISTRIBUTIONS,
        datetime(2024, 1, 1, tzinfo=UTC),
    )


def test_baseline_cli_consumes_experiment_and_verified_dataset(tmp_path: Path) -> None:
    stored = canonical_store_dataset(tmp_path / "cache")
    experiment = ExperimentSpec(
        experiment_id="baseline-cli-fixture",
        dataset_id=stored.manifest.dataset_id,
        data_sha256=stored.manifest.sha256,
        strategy=monthly_spec(),
        failure_rules=(
            FailureRule(
                rule_id="drawdown",
                family="maximum_drawdown",
                threshold=0.2,
                window_rows=None,
            ),
        ),
        seed=17,
        timeout_seconds=30.0,
        code_version="gate-3",
        numeric_tolerance=1e-9,
        transaction_cost_bps=5.0,
    )
    experiment_path = tmp_path / "experiment.json"
    experiment_path.write_text(experiment.model_dump_json(), encoding="utf-8")

    invocation = CliRunner().invoke(
        app,
        ["baseline", str(experiment_path), str(stored.manifest_path)],
    )

    assert invocation.exit_code == 0, invocation.output
    payload = json.loads(invocation.stdout)
    assert payload["experiment_id"] == experiment.experiment_id
    assert payload["data_sha256"] == stored.manifest.sha256
    assert payload["metrics"]["observation_count"] == 21
    assert payload["transaction_cost_bps"] == 5.0
