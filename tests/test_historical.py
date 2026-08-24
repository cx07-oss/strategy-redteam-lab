"""Deterministic acceptance tests for Gate 4 historical attack discovery."""

from __future__ import annotations

import ast
import inspect
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pytest

import strategy_redteam.historical as historical_module
from strategy_redteam import (
    DataManifest,
    ExperimentSpec,
    FailureRule,
    HistoricalScanValidationError,
    StoredDataset,
    StrategySpec,
    scan_historical_failures,
)

DATA_HASH = "4" * 64


def external_spec() -> StrategySpec:
    return StrategySpec(
        strategy_id="fixed-half-half",
        kind="external_weights",
        symbols=("SPY", "TLT"),
        target_weights=None,
        rebalance_frequency="external",
    )


@dataclass(frozen=True)
class FixedHalfStrategy:
    spec: StrategySpec

    def target_weights(self, dataset: StoredDataset) -> pd.DataFrame:
        return pd.DataFrame(
            {"SPY": 0.5, "TLT": 0.5},
            index=dataset.data.index,
        ).rename_axis(columns="symbol")


def make_dataset(spy_returns: np.ndarray, tlt_returns: np.ndarray) -> StoredDataset:
    assert spy_returns.shape == tlt_returns.shape
    dates = pd.date_range(
        "2020-01-02",
        periods=len(spy_returns) + 1,
        freq="B",
        tz="UTC",
        name="date",
    )
    spy_prices = np.concatenate(([100.0], 100.0 * np.cumprod(1.0 + spy_returns)))
    tlt_prices = np.concatenate(([100.0], 100.0 * np.cumprod(1.0 + tlt_returns)))
    columns = pd.MultiIndex.from_tuples(
        [("SPY", "close"), ("TLT", "close")],
        names=("symbol", "field"),
    )
    data = pd.DataFrame(
        np.column_stack((spy_prices, tlt_prices)),
        index=dates,
        columns=columns,
    )
    manifest = DataManifest(
        dataset_id="constructed-history",
        provider="constructed-test-provider",
        source_identifiers={"SPY": "constructed:SPY", "TLT": "constructed:TLT"},
        symbols=("SPY", "TLT"),
        requested_start_date=dates[0].date(),
        requested_end_date=dates[-1].date(),
        start_date=dates[0].date(),
        end_date=dates[-1].date(),
        adjustment_policy="splits_and_distributions",
        calendar_policy="fixed business-day test calendar",
        missing_data_policy="reject",
        row_count=len(dates),
        columns=("date", "SPY.close", "TLT.close"),
        retrieved_at=datetime(2020, 1, 1, tzinfo=UTC),
        media_type="application/vnd.apache.parquet",
        byte_length=1,
        sha256=DATA_HASH,
    )
    return StoredDataset(
        manifest=manifest,
        data=data,
        dataset_path=Path("constructed.parquet"),
        manifest_path=Path("constructed.json"),
        manifest_sha256="5" * 64,
    )


def experiment(
    failure_rules: tuple[FailureRule, ...],
    *,
    top_k: int = 3,
    dataset_id: str = "constructed-history",
) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="historical-gate-4",
        dataset_id=dataset_id,
        data_sha256=DATA_HASH,
        strategy=external_spec(),
        failure_rules=failure_rules,
        seed=23,
        timeout_seconds=30.0,
        code_version="gate-4-test-code",
        numeric_tolerance=1e-12,
        top_k=top_k,
    )


def all_rules() -> tuple[FailureRule, ...]:
    return (
        FailureRule(
            rule_id="drawdown",
            family="maximum_drawdown",
            threshold=0.025,
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
            threshold=2.0,
            window_rows=20,
        ),
    )


def test_known_worst_window_dates_correlation_and_loss_contributions() -> None:
    earned_rows = 180
    spy = np.zeros(earned_rows, dtype=np.float64)
    tlt = np.zeros(earned_rows, dtype=np.float64)
    inverse_pattern = np.tile(np.array([0.005, -0.005]), 10)
    spy[:20] = inverse_pattern
    tlt[:20] = -inverse_pattern

    crisis_start = 140
    crisis = np.tile(np.array([-0.015, -0.005]), 10)
    spy[crisis_start : crisis_start + 20] = crisis
    tlt[crisis_start : crisis_start + 20] = crisis
    recovery_position = crisis_start + 20
    spy[recovery_position] = 0.25
    tlt[recovery_position] = 0.25

    dataset = make_dataset(spy, tlt)
    specification = experiment(all_rules())
    strategy = FixedHalfStrategy(external_spec())

    first = scan_historical_failures(dataset, strategy, specification)
    second = scan_historical_failures(dataset, strategy, specification)

    assert first == second
    assert len(first) == 1
    result = first[0]
    evidence = result.historical_window
    assert evidence is not None
    dates = dataset.data.index
    expected_start = dates[crisis_start + 1].date()
    expected_trough = dates[crisis_start + 20].date()
    assert evidence.window_rows == 20
    assert evidence.start_date == expected_start
    assert evidence.end_date == expected_trough
    assert evidence.breach_onset_date == dates[crisis_start + 3].date()
    assert evidence.loss_start_date == expected_start
    assert evidence.trough_date == expected_trough
    assert evidence.recovery_date == dates[recovery_position + 1].date()

    expected_loss = 1.0 - float(np.prod(1.0 + crisis))
    expected_asset_return = float(np.prod(1.0 + crisis) - 1.0)
    expected_volatility = float(crisis.std(ddof=1) * math.sqrt(252.0))
    assert result.metrics is not None
    assert result.metrics.total_return == pytest.approx(-expected_loss, abs=1e-14)
    assert result.metrics.maximum_drawdown == pytest.approx(expected_loss, abs=1e-14)
    assert evidence.portfolio_loss_to_trough == pytest.approx(expected_loss, abs=1e-14)
    assert evidence.asset_returns["SPY"] == pytest.approx(expected_asset_return, abs=1e-14)
    assert evidence.asset_returns["TLT"] == pytest.approx(expected_asset_return, abs=1e-14)
    assert evidence.asset_realized_volatilities["SPY"] == pytest.approx(
        expected_volatility,
        abs=1e-14,
    )
    assert evidence.spy_tlt_correlation == pytest.approx(1.0, abs=1e-14)
    assert float(np.corrcoef(spy[:20], tlt[:20])[0, 1]) == pytest.approx(-1.0)
    assert evidence.total_turnover == pytest.approx(0.0, abs=1e-14)
    assert evidence.total_transaction_cost == 0.0
    assert evidence.asset_loss_contributions["SPY"] == pytest.approx(
        expected_loss / 2.0,
        abs=1e-14,
    )
    assert evidence.asset_loss_contributions["TLT"] == pytest.approx(
        expected_loss / 2.0,
        abs=1e-14,
    )
    assert evidence.transaction_cost_loss_contribution == 0.0

    assert result.dataset_id == dataset.manifest.dataset_id
    assert result.strategy_id == specification.strategy.strategy_id
    assert result.data_sha256 == dataset.manifest.sha256
    assert result.code_version == specification.code_version
    assert len(result.config_sha256) == 64
    assert result.rank == 1
    assert tuple(breach.rule_id for breach in result.breaches) == (
        "drawdown",
        "rolling-loss",
    )


def test_configured_lengths_are_scanned_and_overlaps_are_deduplicated() -> None:
    earned_rows = 920
    returns = np.zeros(earned_rows, dtype=np.float64)
    episodes = ((100, 20, -0.01), (400, 60, -0.004), (750, 126, -0.002))
    for start, length, daily_return in episodes:
        returns[start : start + length] = daily_return
        returns[start + length] = (1.0 + daily_return) ** (-length) - 1.0
    dataset = make_dataset(returns, returns)
    drawdown_only = (
        FailureRule(
            rule_id="drawdown",
            family="maximum_drawdown",
            threshold=0.03,
            window_rows=None,
        ),
    )

    results = scan_historical_failures(
        dataset,
        FixedHalfStrategy(external_spec()),
        experiment(drawdown_only),
    )

    assert len(results) == 3
    assert len(results) <= 3
    selected_lengths = {
        result.historical_window.window_rows
        for result in results
        if result.historical_window
    }
    assert selected_lengths == {
        20,
        60,
        126,
    }
    intervals = sorted(
        (result.historical_window.start_date, result.historical_window.end_date)
        for result in results
        if result.historical_window is not None
    )
    assert intervals[0][1] < intervals[1][0]
    assert intervals[1][1] < intervals[2][0]
    assert [result.rank for result in results] == [1, 2, 3]


def test_no_failure_returns_empty_bounded_result() -> None:
    pattern = np.tile(np.array([0.002, -0.002]), 70)
    dataset = make_dataset(pattern, -pattern)

    results = scan_historical_failures(
        dataset,
        FixedHalfStrategy(external_spec()),
        experiment(all_rules()),
    )

    assert results == ()


def test_scanner_rejects_provenance_mismatch_before_calculation() -> None:
    dataset = make_dataset(np.zeros(126), np.zeros(126))
    with pytest.raises(HistoricalScanValidationError, match="dataset_id"):
        scan_historical_failures(
            dataset,
            FixedHalfStrategy(external_spec()),
            experiment(all_rules(), dataset_id="wrong-dataset"),
        )


def test_scanner_source_vectorizes_dates_and_bounds_object_construction() -> None:
    source = inspect.getsource(historical_module)
    assert "sliding_window_view" in source
    assert ".iterrows(" not in source
    assert ".itertuples(" not in source
    assert ".rolling.apply(" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            iterator = ast.get_source_segment(source, node.iter) or ""
            assert ".index" not in iterator
            assert "DatetimeIndex" not in iterator
