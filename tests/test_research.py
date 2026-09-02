"""Focused deterministic acceptance tests for MVP-1 research helpers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from strategy_redteam.cli import app
from strategy_redteam.research import (
    RegimeConfig,
    TemporalSplitConfig,
    WalkForwardConfig,
    benchmark_result,
    benchmark_returns,
    chronological_split,
    expanding_walk_forward,
    fit_regime_model,
    performance_metrics,
    regime_conditioned_metrics,
    regime_features,
    stress_surface,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "example_60_40.yaml"
FIXTURE_MANIFEST = (
    ROOT / "tests" / "fixtures" / "offline-cache" / "manifests" / "correlation-break.json"
)


def _returns(rows: int = 80) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="B", tz="UTC")
    values = np.linspace(-0.01, 0.012, rows)
    return pd.DataFrame({"SPY": values, "TLT": values[::-1] / 2.0}, index=index)


def test_metrics_and_edge_cases_are_explicit() -> None:
    metrics = performance_metrics(pd.Series([0.1, -0.05, 0.0]))
    assert metrics.total_return == pytest.approx(0.045)
    assert metrics.maximum_drawdown == pytest.approx(0.05)
    assert metrics.number_of_trades == 2
    assert performance_metrics(pd.Series([], dtype=float)).sharpe_ratio is None
    assert performance_metrics(pd.Series([0.0, 0.0])).sharpe_ratio is None
    assert performance_metrics(pd.Series([0.1, 0.2])).profit_factor is None
    with pytest.raises(ValueError, match="finite"):
        performance_metrics(pd.Series([float("nan")]))


def test_chronological_splits_and_walk_forward_never_train_on_test() -> None:
    index = _returns().index
    train, validation, test = chronological_split(index, TemporalSplitConfig())
    assert train.max() < validation.min() < test.min()
    windows = expanding_walk_forward(
        index, WalkForwardConfig(initial_train_rows=20, test_rows=10, step_rows=10)
    )
    assert all(training.max() < testing.min() for training, testing in windows)
    assert windows[-1][1].max() == index.max()


def test_features_are_shifted_and_gmm_is_deterministic_and_train_only() -> None:
    returns = _returns()
    features = regime_features(returns, RegimeConfig(n_regimes=2))
    changed_future = returns.copy()
    changed_future.iloc[-1] = 0.99
    assert (
        regime_features(changed_future, RegimeConfig(n_regimes=2))
        .iloc[-1]
        .equals(features.iloc[-1])
    )
    train = features.iloc[:40]
    first = fit_regime_model(train, RegimeConfig(n_regimes=2), seed=7)
    second = fit_regime_model(train, RegimeConfig(n_regimes=2), seed=7)
    assert first.predict(features.iloc[40:]).equals(second.predict(features.iloc[40:]))
    assert first.scaler.mean_[0] == pytest.approx(train.iloc[:, 0].mean())


def test_benchmark_regime_summaries_and_surface_are_structured() -> None:
    assets = _returns()
    strategy = assets.mean(axis=1)
    benchmark = benchmark_returns(assets, "buy_and_hold")
    result = benchmark_result(strategy, benchmark, "buy_and_hold")
    assert result.excess_return == pytest.approx(0.0)
    labels = pd.Series(np.arange(len(strategy)) % 2, index=strategy.index)
    summaries = regime_conditioned_metrics(strategy, benchmark, labels)
    assert {summary.regime for summary in summaries} == {0, 1}
    surface = stress_surface((1.0, 2.0), (-0.2, 0.2), lambda vol, corr: vol + corr)
    assert len(surface) == 4
    assert surface == stress_surface((1.0, 2.0), (-0.2, 0.2), lambda vol, corr: vol + corr)


def test_canonical_research_cli_exports_repeatable_engine_backed_artifact(tmp_path: Path) -> None:
    runner = CliRunner()
    first = tmp_path / "first"
    second = tmp_path / "second"
    command = [
        "research",
        "run",
        "--experiment",
        str(CONFIG_PATH),
        "--dataset",
        str(FIXTURE_MANIFEST),
    ]
    first_result = runner.invoke(app, [*command, "--output", str(first)])
    second_result = runner.invoke(app, [*command, "--output", str(second)])
    assert first_result.exit_code == second_result.exit_code == 0
    first_payload = (first / "research-result.json").read_bytes()
    assert first_payload == (second / "research-result.json").read_bytes()
    payload = json.loads(first_payload)
    assert payload["costs"]["net_return"] < payload["costs"]["gross_return"]
    assert payload["costs"]["total_trading_cost"] > 0.0
    assert len(payload["walk_forward"]) == 2
    assert len(payload["regime_assignments"]) > 0
    assert len(payload["stress_surface"]) == 4
