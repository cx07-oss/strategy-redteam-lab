"""Leakage-safe quantitative research helpers.

This module deliberately contains no strategy selection or model-generated
numbers.  It turns already-earned deterministic return series into typed,
reproducible research evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from pydantic import Field, model_validator
from sklearn.mixture import GaussianMixture  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from strategy_redteam.backtest import BacktestResult, run_backtest
from strategy_redteam.data import StoredDataset
from strategy_redteam.domain import (
    ContractModel,
    DataManifest,
    ExperimentSpec,
    StressComponent,
    StressFamily,
    StressScenario,
    Symbol,
)
from strategy_redteam.strategy import Strategy, strategy_from_spec
from strategy_redteam.stress import run_stressed_backtest


class ExecutionCostAssumptions(ContractModel):
    """Per-unit-turnover costs in basis points; all costs reduce net return."""

    commission_bps: float = Field(default=0.0, ge=0.0, lt=10_000.0)
    spread_bps: float = Field(default=0.0, ge=0.0, lt=10_000.0)
    slippage_bps: float = Field(default=0.0, ge=0.0, lt=10_000.0)

    @property
    def total_bps(self) -> float:
        return self.commission_bps + self.spread_bps + self.slippage_bps


class TemporalSplitConfig(ContractModel):
    train_fraction: float = Field(default=0.6, gt=0.0, lt=1.0)
    validation_fraction: float = Field(default=0.2, gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def fractions_leave_test_data(self) -> TemporalSplitConfig:
        if self.train_fraction + self.validation_fraction >= 1.0:
            raise ValueError("train and validation fractions must leave a test period")
        return self


class WalkForwardConfig(ContractModel):
    initial_train_rows: int = Field(ge=2)
    test_rows: int = Field(default=20, ge=1)
    step_rows: int = Field(default=20, ge=1)


class RegimeConfig(ContractModel):
    n_regimes: int = Field(default=4, ge=2, le=8)
    short_momentum_rows: int = Field(default=5, ge=2, le=60)
    medium_momentum_rows: int = Field(default=20, ge=3, le=252)
    volatility_rows: int = Field(default=20, ge=3, le=252)


class Experiment(ContractModel):
    """MVP-1 wrapper retaining the existing immutable experiment contract."""

    experiment: ExperimentSpec
    initial_capital: float = Field(default=1.0, gt=0.0)
    benchmark: Literal["cash", "buy_and_hold"] = "buy_and_hold"
    costs: ExecutionCostAssumptions = ExecutionCostAssumptions()
    temporal_split: TemporalSplitConfig = TemporalSplitConfig()
    walk_forward: WalkForwardConfig
    regime: RegimeConfig = RegimeConfig()


class PerformanceMetrics(ContractModel):
    total_return: float
    cagr: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    maximum_drawdown: float | None
    calmar_ratio: float | None
    win_rate: float | None
    profit_factor: float | None
    turnover: float | None = None
    exposure: float | None = None
    number_of_trades: int | None = None
    average_trade: float | None = None
    best_trade: float | None = None
    worst_trade: float | None = None
    observation_count: int = Field(ge=0)


class BenchmarkResult(ContractModel):
    benchmark: Literal["cash", "buy_and_hold"]
    strategy_return: float
    benchmark_return: float
    excess_return: float


class RegimeSummary(ContractModel):
    regime: int = Field(ge=0)
    observation_count: int = Field(ge=1)
    strategy_return: float
    benchmark_return: float
    excess_return: float
    volatility: float | None
    sharpe_ratio: float | None
    maximum_drawdown: float | None


class StressSurfacePoint(ContractModel):
    volatility_multiplier: float = Field(gt=0.0)
    correlation_shift: float = Field(ge=-1.0, le=1.0)
    result: float


class CostResult(ContractModel):
    gross_return: float
    net_return: float
    commission_cost: float
    spread_cost: float
    slippage_cost: float
    total_trading_cost: float
    turnover: float


class WalkForwardFoldResult(ContractModel):
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    observation_count: int
    performance: PerformanceMetrics


class RegimeModelMetadata(ContractModel):
    features: tuple[str, ...]
    n_regimes: int
    random_state: int
    training_start: str
    training_end: str
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    component_means: tuple[tuple[float, ...], ...]


class RegimeAssignment(ContractModel):
    date: str
    regime: int = Field(ge=0)


class EquityCurvePoint(ContractModel):
    date: str
    strategy_equity: float = Field(gt=0.0)
    benchmark_equity: float = Field(gt=0.0)
    drawdown: float = Field(ge=0.0, le=1.0)


class ExperimentResult(ContractModel):
    experiment_id: str
    data_manifest: DataManifest
    performance: PerformanceMetrics
    gross_performance: PerformanceMetrics
    costs: CostResult
    benchmark: BenchmarkResult
    temporal_split_dates: tuple[tuple[str, str], tuple[str, str], tuple[str, str]]
    walk_forward: tuple[WalkForwardFoldResult, ...]
    walk_forward_out_of_sample: PerformanceMetrics
    regime_model: RegimeModelMetadata
    regime_assignments: tuple[RegimeAssignment, ...]
    regime_summaries: tuple[RegimeSummary, ...]
    stress_surface: tuple[StressSurfacePoint, ...]
    equity_curve: tuple[EquityCurvePoint, ...]
    seed: int


def _finite_returns(returns: pd.Series) -> pd.Series:
    values = pd.Series(returns, copy=True, dtype=np.float64)
    if not np.isfinite(values.to_numpy()).all() or (values <= -1.0).any():
        raise ValueError("returns must be finite and strictly above -1")
    return values


def performance_metrics(
    returns: pd.Series,
    *,
    turnover: pd.Series | None = None,
    exposure: pd.Series | None = None,
) -> PerformanceMetrics:
    """Compute transparent metrics, returning ``None`` where a ratio is undefined."""
    values = _finite_returns(returns)
    count = len(values)
    if count == 0:
        return PerformanceMetrics(
            total_return=0.0,
            cagr=None,
            annualized_volatility=None,
            sharpe_ratio=None,
            sortino_ratio=None,
            maximum_drawdown=None,
            calmar_ratio=None,
            win_rate=None,
            profit_factor=None,
            observation_count=0,
        )
    wealth = (1.0 + values).cumprod()
    total = float(wealth.iloc[-1] - 1.0)
    annualized = float(values.std(ddof=1) * np.sqrt(252.0)) if count > 1 else None
    cagr = float(wealth.iloc[-1] ** (252.0 / count) - 1.0) if count else None
    mean = float(values.mean())
    daily_std = float(values.std(ddof=1)) if count > 1 else 0.0
    sharpe = None if daily_std == 0.0 else float(mean / daily_std * np.sqrt(252.0))
    downside = values[values < 0.0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = None if downside_std == 0.0 else float(mean / downside_std * np.sqrt(252.0))
    drawdown = 1.0 - wealth / wealth.cummax()
    max_drawdown = float(drawdown.max())
    calmar = None if max_drawdown == 0.0 or cagr is None else float(cagr / max_drawdown)
    gains, losses = values[values > 0.0], values[values < 0.0]
    profit_factor = None if losses.empty else float(gains.sum() / abs(losses.sum()))
    trade_returns = values[values != 0.0]
    return PerformanceMetrics(
        total_return=total,
        cagr=cagr,
        annualized_volatility=annualized,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        maximum_drawdown=max_drawdown,
        calmar_ratio=calmar,
        win_rate=None if trade_returns.empty else float((trade_returns > 0).mean()),
        profit_factor=profit_factor,
        turnover=None if turnover is None else float(turnover.sum()),
        exposure=None if exposure is None else float(exposure.mean()),
        number_of_trades=len(trade_returns),
        average_trade=None if trade_returns.empty else float(trade_returns.mean()),
        best_trade=None if trade_returns.empty else float(trade_returns.max()),
        worst_trade=None if trade_returns.empty else float(trade_returns.min()),
        observation_count=count,
    )


def chronological_split(
    index: pd.Index, config: TemporalSplitConfig
) -> tuple[pd.Index, pd.Index, pd.Index]:
    """Split an already chronologically ordered index without shuffling."""
    if not index.is_monotonic_increasing or len(index) < 3:
        raise ValueError("chronological split requires at least three ordered observations")
    train_end = int(len(index) * config.train_fraction)
    validation_end = train_end + int(len(index) * config.validation_fraction)
    if train_end < 1 or validation_end <= train_end or validation_end >= len(index):
        raise ValueError("split configuration produces an empty partition")
    return index[:train_end], index[train_end:validation_end], index[validation_end:]


def expanding_walk_forward(
    index: pd.Index, config: WalkForwardConfig
) -> tuple[tuple[pd.Index, pd.Index], ...]:
    """Return expanding train / strictly-later test windows."""
    if not index.is_monotonic_increasing:
        raise ValueError("walk-forward index must be chronological")
    windows: list[tuple[pd.Index, pd.Index]] = []
    train_end = config.initial_train_rows
    while train_end < len(index):
        test_end = min(train_end + config.test_rows, len(index))
        windows.append((index[:train_end], index[train_end:test_end]))
        train_end += config.step_rows
    if not windows:
        raise ValueError("walk-forward configuration produces no test observations")
    return tuple(windows)


def regime_features(asset_returns: pd.DataFrame, config: RegimeConfig) -> pd.DataFrame:
    """Generate rolling features shifted one row so each row uses only prior closes."""
    if asset_returns.empty or not asset_returns.index.is_monotonic_increasing:
        raise ValueError("asset returns must be non-empty and chronological")
    if not np.isfinite(asset_returns.to_numpy(dtype=np.float64)).all():
        raise ValueError("asset returns must be finite")
    aggregate = asset_returns.mean(axis=1)
    wealth = (1.0 + aggregate).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    feature_frame = pd.DataFrame(
        {
            "momentum_short": aggregate.rolling(config.short_momentum_rows).sum(),
            "momentum_medium": aggregate.rolling(config.medium_momentum_rows).sum(),
            "realized_volatility": aggregate.rolling(config.volatility_rows).std(ddof=1),
            "rolling_drawdown": drawdown,
        },
        index=asset_returns.index,
    ).shift(1)
    if asset_returns.shape[1] > 1:
        feature_frame["rolling_correlation"] = (
            asset_returns.iloc[:, 0]
            .rolling(config.volatility_rows)
            .corr(asset_returns.iloc[:, 1])
            .shift(1)
        )
    return feature_frame.dropna()


@dataclass(frozen=True)
class FittedRegimeModel:
    scaler: StandardScaler
    model: GaussianMixture
    feature_columns: tuple[str, ...]
    seed: int

    def predict(self, features: pd.DataFrame) -> pd.Series:
        selected = features.loc[:, self.feature_columns]
        labels = self.model.predict(self.scaler.transform(selected))
        return pd.Series(labels, index=selected.index, name="regime", dtype="int64")


def fit_regime_model(
    training_features: pd.DataFrame, config: RegimeConfig, seed: int
) -> FittedRegimeModel:
    if len(training_features) < config.n_regimes:
        raise ValueError("training features must contain at least n_regimes rows")
    scaler = StandardScaler().fit(training_features)
    model = GaussianMixture(n_components=config.n_regimes, random_state=seed, n_init=1).fit(
        scaler.transform(training_features)
    )
    return FittedRegimeModel(scaler, model, tuple(training_features.columns), seed)


def benchmark_returns(
    asset_returns: pd.DataFrame, benchmark: Literal["cash", "buy_and_hold"]
) -> pd.Series:
    if benchmark == "cash":
        return pd.Series(0.0, index=asset_returns.index, name="benchmark_return")
    return asset_returns.mean(axis=1).rename("benchmark_return")


def benchmark_result(
    strategy_returns: pd.Series,
    benchmark_returns_: pd.Series,
    benchmark: Literal["cash", "buy_and_hold"],
) -> BenchmarkResult:
    strategy = _finite_returns(strategy_returns)
    reference = _finite_returns(benchmark_returns_.reindex(strategy.index))
    strategy_total = float((1.0 + strategy).prod() - 1.0)
    benchmark_total = float((1.0 + reference).prod() - 1.0)
    return BenchmarkResult(
        benchmark=benchmark,
        strategy_return=strategy_total,
        benchmark_return=benchmark_total,
        excess_return=strategy_total - benchmark_total,
    )


def regime_conditioned_metrics(
    strategy_returns: pd.Series, benchmark_returns_: pd.Series, labels: pd.Series
) -> tuple[RegimeSummary, ...]:
    joined = pd.concat(
        [
            strategy_returns.rename("strategy"),
            benchmark_returns_.rename("benchmark"),
            labels.rename("regime"),
        ],
        axis=1,
    ).dropna()
    summaries: list[RegimeSummary] = []
    for regime, group in joined.groupby("regime", sort=True):
        metrics = performance_metrics(group["strategy"])
        strategy_total = metrics.total_return
        benchmark_total = float((1.0 + group["benchmark"]).prod() - 1.0)
        summaries.append(
            RegimeSummary(
                regime=int(regime),
                observation_count=len(group),
                strategy_return=strategy_total,
                benchmark_return=benchmark_total,
                excess_return=strategy_total - benchmark_total,
                volatility=metrics.annualized_volatility,
                sharpe_ratio=metrics.sharpe_ratio,
                maximum_drawdown=metrics.maximum_drawdown,
            )
        )
    return tuple(summaries)


def stress_surface(
    volatility_multipliers: tuple[float, ...],
    correlation_shifts: tuple[float, ...],
    evaluate: Callable[[float, float], float],
) -> tuple[StressSurfacePoint, ...]:
    """Evaluate a bounded, caller-owned deterministic stress grid in stable order."""
    return tuple(
        StressSurfacePoint(
            volatility_multiplier=volatility,
            correlation_shift=correlation,
            result=float(evaluate(volatility, correlation)),
        )
        for volatility in volatility_multipliers
        for correlation in correlation_shifts
    )


def _date_range(index: pd.Index) -> tuple[str, str]:
    return str(index[0].date()), str(index[-1].date())


def _cost_result(backtest: BacktestResult) -> CostResult:
    gross = float((1.0 + backtest.gross_portfolio_returns.iloc[1:]).prod() - 1.0)
    net = float((1.0 + backtest.portfolio_returns.iloc[1:]).prod() - 1.0)
    return CostResult(
        gross_return=gross,
        net_return=net,
        commission_cost=float(backtest.commission_costs.sum()),
        spread_cost=float(backtest.spread_costs.sum()),
        slippage_cost=float(backtest.slippage_costs.sum()),
        total_trading_cost=float(backtest.transaction_costs.sum()),
        turnover=float(backtest.turnover.sum()),
    )


def run_research_experiment(
    dataset: StoredDataset,
    experiment: Experiment,
    *,
    volatility_multipliers: tuple[float, ...] = (1.0, 1.5),
    correlation_shifts: tuple[float, ...] = (0.0, -0.25),
) -> ExperimentResult:
    """Run one deterministic end-to-end research evaluation over verified data.

    Walk-forward folds replay the actual strategy on each expanding train-plus-test
    prefix, then retain only the strictly later test returns.  The regime model is
    fitted only to the chronological training feature rows.
    """
    spec = experiment.experiment
    if (
        spec.dataset_id != dataset.manifest.dataset_id
        or spec.data_sha256 != dataset.manifest.sha256
    ):
        raise ValueError("experiment provenance does not match the verified dataset")
    strategy: Strategy = strategy_from_spec(spec.strategy, spec.numeric_tolerance)
    costs = experiment.costs
    baseline = run_backtest(
        dataset,
        strategy,
        numeric_tolerance=spec.numeric_tolerance,
        failure_rules=spec.failure_rules,
        commission_bps=costs.commission_bps,
        spread_bps=costs.spread_bps,
        slippage_bps=costs.slippage_bps,
    )
    earned = baseline.portfolio_returns.iloc[1:]
    gross = baseline.gross_portfolio_returns.iloc[1:]
    benchmark = benchmark_returns(baseline.asset_returns.iloc[1:], experiment.benchmark)
    strategy_equity = (1.0 + earned).cumprod()
    benchmark_equity = (1.0 + benchmark).cumprod()
    drawdown = 1.0 - strategy_equity / strategy_equity.cummax()
    train_dates, validation_dates, test_dates = chronological_split(
        earned.index, experiment.temporal_split
    )
    windows = expanding_walk_forward(earned.index, experiment.walk_forward)
    fold_results: list[WalkForwardFoldResult] = []
    oos_returns: list[pd.Series] = []
    for training, testing in windows:
        prefix = dataset.data.loc[: testing[-1]].copy()
        prefix_dataset = StoredDataset(
            manifest=dataset.manifest,
            data=prefix,
            dataset_path=dataset.dataset_path,
            manifest_path=dataset.manifest_path,
            manifest_sha256=dataset.manifest_sha256,
        )
        prefix_strategy = strategy_from_spec(spec.strategy, spec.numeric_tolerance)
        fold = run_backtest(
            prefix_dataset,
            prefix_strategy,
            numeric_tolerance=spec.numeric_tolerance,
            failure_rules=(),
            commission_bps=costs.commission_bps,
            spread_bps=costs.spread_bps,
            slippage_bps=costs.slippage_bps,
        )
        fold_returns = fold.portfolio_returns.loc[testing]
        oos_returns.append(fold_returns)
        fold_results.append(
            WalkForwardFoldResult(
                train_start=_date_range(training)[0],
                train_end=_date_range(training)[1],
                test_start=_date_range(testing)[0],
                test_end=_date_range(testing)[1],
                observation_count=len(fold_returns),
                performance=performance_metrics(fold_returns),
            )
        )
    oos = pd.concat(oos_returns).loc[lambda series: ~series.index.duplicated(keep="first")]
    features = regime_features(baseline.asset_returns.iloc[1:], experiment.regime)
    training_features = features.loc[features.index.intersection(train_dates)]
    model = fit_regime_model(training_features, experiment.regime, spec.seed)
    inference_features = features.loc[features.index > training_features.index[-1]]
    labels = model.predict(inference_features)
    source_correlation = float(
        baseline.asset_returns.iloc[1:, 0].corr(baseline.asset_returns.iloc[1:, 1])
    )
    if not np.isfinite(source_correlation):
        raise ValueError("stress surface requires finite source correlation")

    def evaluate_surface(volatility: float, shift: float) -> float:
        target = source_correlation + shift
        if not -1.0 < target < 1.0:
            raise ValueError("correlation shift creates an invalid target correlation")
        start, end = dataset.data.index[1].date(), dataset.data.index[-1].date()
        scenario = StressScenario(
            scenario_id=f"surface-v{volatility:g}-c{shift:g}".replace(".", "_"),
            evaluation_start=start,
            evaluation_end=end,
            hypothesis="Deterministic stress-surface grid point.",
            components=(
                StressComponent(
                    family=StressFamily.VOLATILITY_MULTIPLIER,
                    start_date=start,
                    end_date=end,
                    symbols=(Symbol.SPY, Symbol.TLT),
                    volatility_multiplier=volatility,
                ),
                StressComponent(
                    family=StressFamily.CORRELATION_TARGET,
                    start_date=start,
                    end_date=end,
                    target_correlation=target,
                ),
            ),
        )
        stressed = run_stressed_backtest(
            dataset,
            strategy_from_spec(spec.strategy, spec.numeric_tolerance),
            scenario,
            costs.total_bps,
            spec.numeric_tolerance,
            spec.failure_rules,
            spec.seed,
        )
        return stressed.stressed_backtest.metrics.total_return

    return ExperimentResult(
        experiment_id=spec.experiment_id,
        data_manifest=dataset.manifest,
        performance=performance_metrics(
            earned,
            turnover=baseline.turnover.iloc[1:],
            exposure=baseline.effective_weights.iloc[1:].sum(axis=1),
        ),
        gross_performance=performance_metrics(gross),
        costs=_cost_result(baseline),
        benchmark=benchmark_result(earned, benchmark, experiment.benchmark),
        temporal_split_dates=(
            _date_range(train_dates),
            _date_range(validation_dates),
            _date_range(test_dates),
        ),
        walk_forward=tuple(fold_results),
        walk_forward_out_of_sample=performance_metrics(oos),
        regime_model=RegimeModelMetadata(
            features=model.feature_columns,
            n_regimes=experiment.regime.n_regimes,
            random_state=spec.seed,
            training_start=_date_range(training_features.index)[0],
            training_end=_date_range(training_features.index)[1],
            scaler_mean=tuple(float(value) for value in model.scaler.mean_),
            scaler_scale=tuple(float(value) for value in model.scaler.scale_),
            component_means=tuple(tuple(float(item) for item in row) for row in model.model.means_),
        ),
        regime_assignments=tuple(
            RegimeAssignment(date=str(date.date()), regime=int(label))
            for date, label in labels.items()
        ),
        regime_summaries=regime_conditioned_metrics(earned, benchmark, labels),
        stress_surface=stress_surface(volatility_multipliers, correlation_shifts, evaluate_surface),
        equity_curve=tuple(
            EquityCurvePoint(
                date=str(timestamp.date()),
                strategy_equity=float(strategy_equity.loc[timestamp]),
                benchmark_equity=float(benchmark_equity.loc[timestamp]),
                drawdown=float(drawdown.loc[timestamp]),
            )
            for timestamp in strategy_equity.index
        ),
        seed=spec.seed,
    )
