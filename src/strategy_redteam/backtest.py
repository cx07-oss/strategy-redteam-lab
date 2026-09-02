"""Vectorized deterministic baseline backtest and failure-rule evaluation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd  # type: ignore[import-untyped]

from strategy_redteam.data import StoredDataset
from strategy_redteam.domain import (
    DEFAULT_NUMERIC_TOLERANCE,
    ROLLING_WINDOW_ROWS,
    FailureBreach,
    FailureRule,
    FailureRuleFamily,
    MetricSet,
    StrategyKind,
    Symbol,
)
from strategy_redteam.strategy import (
    FixedMonthly6040Strategy,
    Strategy,
    close_prices,
    validate_target_weights,
)

if TYPE_CHECKING:
    np: Any
else:
    import numpy as np


class BacktestError(Exception):
    """Base class for typed deterministic backtest failures."""


class BacktestValidationError(BacktestError):
    """Validated inputs cannot produce a finite admissible portfolio path."""


@dataclass(frozen=True)
class NonEvaluableWindows:
    """Rolling windows omitted because their volatility denominator is unavailable."""

    rule_id: str
    window_end_dates: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class FailureEvaluation:
    """Deterministic configured breaches and disclosed non-evaluable windows."""

    breaches: tuple[FailureBreach, ...]
    non_evaluable_windows: tuple[NonEvaluableWindows, ...]


@dataclass(frozen=True)
class BacktestResult:
    """Aligned engine-owned evidence from one deterministic strategy replay."""

    target_weights: pd.DataFrame
    effective_weights: pd.DataFrame
    asset_returns: pd.DataFrame
    asset_contributions: pd.DataFrame
    gross_portfolio_returns: pd.Series
    turnover: pd.Series
    commission_costs: pd.Series
    spread_costs: pd.Series
    slippage_costs: pd.Series
    transaction_costs: pd.Series
    portfolio_returns: pd.Series
    equity_curve: pd.Series
    drawdown_curve: pd.Series
    metrics: MetricSet
    failure_evaluation: FailureEvaluation


def _effective_weights(target_weights: pd.DataFrame) -> pd.DataFrame:
    """Apply the sole execution-timing convention.

    A target stamped at close ``t`` uses information through that close and becomes
    effective only for the observed return from ``t`` to ``t+1``. The first dataset
    row therefore has zero effective exposure and earns no portfolio return.
    """
    effective = target_weights.shift(1, fill_value=0.0)
    effective.columns.name = "symbol"
    return effective


def _rolling_compounded_returns(returns: pd.Series) -> pd.Series:
    log_gross = np.log1p(returns)
    return np.expm1(
        log_gross.rolling(
            ROLLING_WINDOW_ROWS,
            min_periods=ROLLING_WINDOW_ROWS,
        ).sum()
    )


def _drawdown_series(equity_curve: pd.Series) -> pd.Series:
    return 1.0 - equity_curve.div(equity_curve.cummax())


def _metrics(portfolio_returns: pd.Series, equity_curve: pd.Series) -> MetricSet:
    earned_returns = portfolio_returns.iloc[1:]
    rolling_returns = _rolling_compounded_returns(earned_returns).dropna()
    worst_rolling = float(rolling_returns.min()) if not rolling_returns.empty else 0.0
    annualized_volatility = (
        float(earned_returns.std(ddof=1) * math.sqrt(252.0))
        if len(earned_returns.index) > 1
        else 0.0
    )
    values = np.asarray(
        [
            float(equity_curve.iloc[-1] - 1.0),
            float(_drawdown_series(equity_curve).max()),
            worst_rolling,
            annualized_volatility,
        ],
        dtype=np.float64,
    )
    if not np.isfinite(values).all():
        raise BacktestValidationError("portfolio metrics must be finite")
    return MetricSet(
        total_return=float(values[0]),
        maximum_drawdown=float(values[1]),
        worst_rolling_20_day_return=float(values[2]),
        annualized_volatility=float(values[3]),
        observation_count=len(earned_returns.index),
    )


def _affected_symbols(
    effective_weights: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    numeric_tolerance: float,
) -> tuple[Symbol, ...]:
    active = effective_weights.loc[start:end].abs().gt(numeric_tolerance).any(axis="index")
    affected = tuple(Symbol(label) for label in active.index[active].tolist())
    if not affected:
        raise BacktestValidationError("a breached window has no affected positions")
    return affected


def _window_start(index: pd.DatetimeIndex, end: pd.Timestamp) -> pd.Timestamp:
    location = index.get_loc(end)
    if not isinstance(location, int):
        raise BacktestValidationError("rolling result dates must be unique")
    return index[location - ROLLING_WINDOW_ROWS + 1]


def _breach(
    rule: FailureRule,
    observed_value: float,
    onset: pd.Timestamp,
    worst_start: pd.Timestamp,
    worst_end: pd.Timestamp,
    affected_symbols: tuple[Symbol, ...],
    recovery: pd.Timestamp | None = None,
) -> FailureBreach:
    return FailureBreach(
        rule_id=rule.rule_id,
        family=rule.family,
        observed_value=float(observed_value),
        threshold=float(rule.threshold),
        normalized_excess=float(observed_value / rule.threshold - 1.0),
        onset_date=onset.date(),
        worst_window_start=worst_start.date(),
        worst_window_end=worst_end.date(),
        trough_date=(
            worst_end.date() if rule.family is FailureRuleFamily.MAXIMUM_DRAWDOWN else None
        ),
        recovery_date=None if recovery is None else recovery.date(),
        affected_symbols=affected_symbols,
    )


def evaluate_failure_rules(
    portfolio_returns: pd.Series,
    equity_curve: pd.Series,
    effective_weights: pd.DataFrame,
    failure_rules: Sequence[FailureRule],
    numeric_tolerance: float,
    baseline_portfolio_returns: pd.Series | None = None,
) -> FailureEvaluation:
    """Evaluate configured rules using only trailing or contemporaneous observations."""
    if baseline_portfolio_returns is None:
        baseline_portfolio_returns = portfolio_returns
    if not baseline_portfolio_returns.index.equals(portfolio_returns.index):
        raise BacktestValidationError("baseline returns must exactly align with evaluated returns")
    baseline_values = baseline_portfolio_returns.to_numpy(dtype=np.float64, copy=False)
    if not np.isfinite(baseline_values).all() or (baseline_values <= -1.0).any():
        raise BacktestValidationError("baseline portfolio returns must be finite and above -1")

    earned_returns = portfolio_returns.iloc[1:]
    baseline_earned = baseline_portfolio_returns.iloc[1:]
    drawdowns = _drawdown_series(equity_curve)
    rolling_returns = _rolling_compounded_returns(earned_returns)
    evaluated_volatility = earned_returns.rolling(
        ROLLING_WINDOW_ROWS,
        min_periods=ROLLING_WINDOW_ROWS,
    ).std(ddof=1) * math.sqrt(252.0)
    baseline_volatility = baseline_earned.rolling(
        ROLLING_WINDOW_ROWS,
        min_periods=ROLLING_WINDOW_ROWS,
    ).std(ddof=1) * math.sqrt(252.0)

    breaches: list[FailureBreach] = []
    non_evaluable: list[NonEvaluableWindows] = []
    for rule in failure_rules:
        recovery: pd.Timestamp | None = None
        if rule.family is FailureRuleFamily.MAXIMUM_DRAWDOWN:
            violating = drawdowns.gt(rule.threshold)
            if not violating.any():
                continue
            onset = drawdowns.index[violating.to_numpy()][0]
            worst_end = drawdowns.idxmax()
            preceding_equity = equity_curve.loc[:worst_end]
            high_water_mark = preceding_equity.max()
            worst_start = preceding_equity.index[preceding_equity.eq(high_water_mark)][-1]
            observed = float(drawdowns.loc[worst_end])
            recovery_mask = equity_curve.loc[worst_end:].ge(high_water_mark - numeric_tolerance)
            if recovery_mask.any():
                recovery = recovery_mask.index[recovery_mask.to_numpy()][0]
        elif rule.family is FailureRuleFamily.ROLLING_20_DAY_LOSS:
            violating = rolling_returns.lt(-rule.threshold)
            if not violating.any():
                continue
            onset = rolling_returns.index[violating.fillna(False).to_numpy()][0]
            worst_end = rolling_returns.idxmin()
            worst_start = _window_start(earned_returns.index, worst_end)
            observed = float(-rolling_returns.loc[worst_end])
        else:
            evaluable = (
                evaluated_volatility.notna()
                & baseline_volatility.notna()
                & baseline_volatility.gt(0.0)
            )
            omitted_dates = earned_returns.index[~evaluable.to_numpy()]
            if not omitted_dates.empty:
                non_evaluable.append(
                    NonEvaluableWindows(
                        rule_id=rule.rule_id,
                        window_end_dates=tuple(omitted_dates.strftime("%Y-%m-%d").tolist()),
                        reason="20-row baseline volatility is zero or unavailable",
                    )
                )
            volatility_multiple = evaluated_volatility.div(baseline_volatility).where(evaluable)
            violating = volatility_multiple.gt(rule.threshold)
            if not violating.any():
                continue
            onset = volatility_multiple.index[violating.fillna(False).to_numpy()][0]
            worst_end = volatility_multiple.idxmax()
            worst_start = _window_start(earned_returns.index, worst_end)
            observed = float(volatility_multiple.loc[worst_end])

        affected = _affected_symbols(
            effective_weights,
            worst_start,
            worst_end,
            numeric_tolerance,
        )
        breaches.append(
            _breach(
                rule,
                observed,
                onset,
                worst_start,
                worst_end,
                affected,
                recovery,
            )
        )
    return FailureEvaluation(tuple(breaches), tuple(non_evaluable))


def _validate_backtest_context(
    dataset: StoredDataset,
    strategy: Strategy,
    transaction_cost_bps: float,
    numeric_tolerance: float,
) -> None:
    if strategy.spec.symbols != dataset.manifest.symbols:
        raise BacktestValidationError("strategy symbols must match the dataset manifest")
    if not np.isfinite(transaction_cost_bps) or not 0.0 <= transaction_cost_bps < 10_000.0:
        raise BacktestValidationError("transaction_cost_bps must be finite in [0, 10000)")
    if not np.isfinite(numeric_tolerance) or not 0.0 < numeric_tolerance < 1.0:
        raise BacktestValidationError("numeric_tolerance must be finite and in (0, 1)")
    if len(dataset.data.index) < 2:
        raise BacktestValidationError("backtesting requires at least two dataset rows")


def validate_supplied_asset_returns(
    dataset: StoredDataset,
    asset_returns: pd.DataFrame,
) -> pd.DataFrame:
    expected_columns = pd.Index(
        [symbol.value for symbol in dataset.manifest.symbols],
        name="symbol",
    )
    if not isinstance(asset_returns, pd.DataFrame):
        raise BacktestValidationError("asset_returns must be a pandas DataFrame")
    if not asset_returns.index.equals(dataset.data.index):
        raise BacktestValidationError("asset-return dates must exactly match dataset dates")
    if not asset_returns.columns.equals(expected_columns):
        raise BacktestValidationError("asset-return columns must match manifest symbols in order")
    if any(
        not pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype)
        for dtype in asset_returns.dtypes
    ):
        raise BacktestValidationError("asset returns must be numeric")
    canonical = asset_returns.astype(np.float64).copy()
    values = canonical.to_numpy(copy=False)
    if not np.isfinite(values).all() or (values <= -1.0).any():
        raise BacktestValidationError("asset returns must be finite and above -1")
    if not np.equal(values[0], 0.0).all():
        raise BacktestValidationError("the first dataset row cannot contain an asset return")
    return canonical


def _run_backtest_inputs(
    dataset: StoredDataset,
    strategy: Strategy,
    targets: pd.DataFrame,
    asset_returns: pd.DataFrame,
    transaction_cost_bps: float,
    commission_bps: float,
    spread_bps: float,
    slippage_bps: float,
    numeric_tolerance: float,
    failure_rules: Sequence[FailureRule],
    baseline_portfolio_returns: pd.Series | None,
) -> BacktestResult:
    targets = validate_target_weights(
        targets,
        dataset,
        strategy.spec,
        numeric_tolerance,
    )
    effective_weights = _effective_weights(targets)
    asset_contributions = effective_weights.mul(asset_returns)
    gross_returns = asset_contributions.sum(axis="columns").rename("gross_portfolio_return")
    if not np.isfinite(gross_returns.to_numpy()).all() or gross_returns.le(-1.0).any():
        raise BacktestValidationError("gross portfolio returns must be finite and above -1")

    previous_effective = effective_weights.shift(1, fill_value=0.0)
    previous_asset_gross = (1.0 + asset_returns).shift(1, fill_value=1.0)
    previous_portfolio_gross = (1.0 + gross_returns).shift(1, fill_value=1.0)
    pretrade_weights = previous_effective.mul(previous_asset_gross).div(
        previous_portfolio_gross,
        axis="index",
    )
    turnover = effective_weights.sub(pretrade_weights).abs().sum(axis="columns")
    turnover = turnover.mask(turnover.le(numeric_tolerance), 0.0).rename("turnover")
    commission_costs = (turnover * (commission_bps / 10_000.0)).rename("commission_cost")
    spread_costs = (turnover * (spread_bps / 10_000.0)).rename("spread_cost")
    slippage_costs = (turnover * (slippage_bps / 10_000.0)).rename("slippage_cost")
    transaction_costs = (turnover * (transaction_cost_bps / 10_000.0)).rename("transaction_cost")
    portfolio_returns = gross_returns.sub(transaction_costs).rename("portfolio_return")
    portfolio_values = portfolio_returns.to_numpy(dtype=np.float64, copy=False)
    if not np.isfinite(portfolio_values).all() or (portfolio_values <= -1.0).any():
        raise BacktestValidationError("net portfolio returns must be finite and above -1")
    equity_curve = (1.0 + portfolio_returns).cumprod().rename("equity")

    metrics = _metrics(portfolio_returns, equity_curve)
    evaluation = evaluate_failure_rules(
        portfolio_returns,
        equity_curve,
        effective_weights,
        failure_rules,
        numeric_tolerance,
        baseline_portfolio_returns,
    )
    return BacktestResult(
        target_weights=targets,
        effective_weights=effective_weights,
        asset_returns=asset_returns,
        asset_contributions=asset_contributions,
        gross_portfolio_returns=gross_returns,
        turnover=turnover,
        commission_costs=commission_costs,
        spread_costs=spread_costs,
        slippage_costs=slippage_costs,
        transaction_costs=transaction_costs,
        portfolio_returns=portfolio_returns,
        equity_curve=equity_curve,
        drawdown_curve=_drawdown_series(equity_curve).rename("drawdown"),
        metrics=metrics,
        failure_evaluation=evaluation,
    )


def run_backtest(
    dataset: StoredDataset,
    strategy: Strategy,
    transaction_cost_bps: float = 0.0,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    failure_rules: Sequence[FailureRule] = (),
    baseline_portfolio_returns: pd.Series | None = None,
    *,
    commission_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> BacktestResult:
    """Run one vectorized daily replay over a manifest-verified dataset."""
    if any(
        not np.isfinite(value) or value < 0.0
        for value in (commission_bps, spread_bps, slippage_bps)
    ):
        raise BacktestValidationError("detailed cost assumptions must be finite and non-negative")
    detailed_cost_bps = commission_bps + spread_bps + slippage_bps
    if detailed_cost_bps and transaction_cost_bps:
        raise BacktestValidationError(
            "use either transaction_cost_bps or detailed cost assumptions"
        )
    applied_cost_bps = detailed_cost_bps or transaction_cost_bps
    _validate_backtest_context(
        dataset,
        strategy,
        applied_cost_bps,
        numeric_tolerance,
    )
    prices = close_prices(dataset)
    asset_returns = prices.pct_change(fill_method=None).fillna(0.0)
    asset_returns.columns.name = "symbol"
    return _run_backtest_inputs(
        dataset,
        strategy,
        strategy.target_weights(dataset),
        asset_returns,
        applied_cost_bps,
        commission_bps,
        spread_bps,
        slippage_bps,
        numeric_tolerance,
        failure_rules,
        baseline_portfolio_returns,
    )


def run_backtest_with_asset_returns(
    dataset: StoredDataset,
    strategy: Strategy,
    asset_returns: pd.DataFrame,
    transaction_cost_bps: float = 0.0,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    failure_rules: Sequence[FailureRule] = (),
    baseline_portfolio_returns: pd.Series | None = None,
    *,
    commission_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> BacktestResult:
    """Replay a full in-memory stressed return path without mutating source prices."""
    if any(
        not np.isfinite(value) or value < 0.0
        for value in (commission_bps, spread_bps, slippage_bps)
    ):
        raise BacktestValidationError("detailed cost assumptions must be finite and non-negative")
    detailed_cost_bps = commission_bps + spread_bps + slippage_bps
    if detailed_cost_bps and transaction_cost_bps:
        raise BacktestValidationError(
            "use either transaction_cost_bps or detailed cost assumptions"
        )
    applied_cost_bps = detailed_cost_bps or transaction_cost_bps
    _validate_backtest_context(
        dataset,
        strategy,
        applied_cost_bps,
        numeric_tolerance,
    )
    canonical_returns = validate_supplied_asset_returns(dataset, asset_returns)
    if strategy.spec.kind is StrategyKind.MONTHLY_60_40:
        if not isinstance(strategy, FixedMonthly6040Strategy):
            raise BacktestValidationError(
                "monthly_60_40 stressed replay requires FixedMonthly6040Strategy"
            )
        return_values = canonical_returns.to_numpy(dtype=np.float64, copy=False)
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            relative_values = np.cumprod(1.0 + return_values, axis=0)
        relative_prices = pd.DataFrame(
            relative_values,
            index=canonical_returns.index,
            columns=canonical_returns.columns,
        )
        targets = strategy.target_weights_from_prices(dataset, relative_prices)
    else:
        targets = strategy.target_weights(dataset)
    return _run_backtest_inputs(
        dataset,
        strategy,
        targets,
        canonical_returns,
        applied_cost_bps,
        commission_bps,
        spread_bps,
        slippage_bps,
        numeric_tolerance,
        failure_rules,
        baseline_portfolio_returns,
    )
