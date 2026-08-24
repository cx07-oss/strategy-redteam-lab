"""Bounded vectorized discovery of unchanged historical failure windows."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import pandas as pd  # type: ignore[import-untyped]

from strategy_redteam.backtest import BacktestResult, run_backtest
from strategy_redteam.data import StoredDataset
from strategy_redteam.domain import (
    ROLLING_WINDOW_ROWS,
    ExperimentSpec,
    FailureBreach,
    FailureRule,
    FailureRuleFamily,
    HistoricalWindowEvidence,
    MetricSet,
    ResultStatus,
    StressResult,
    Symbol,
)
from strategy_redteam.strategy import Strategy

if TYPE_CHECKING:
    np: Any
else:
    import numpy as np

HISTORICAL_SCANNER_VERSION = "historical-scanner-1.0"


class HistoricalScanError(Exception):
    """Base class for deterministic historical-discovery failures."""


class HistoricalScanValidationError(HistoricalScanError):
    """Dataset, strategy, or experiment provenance is inconsistent."""


@dataclass(frozen=True)
class _LossEpisode:
    """Selected peak-to-trough details calculated without engine-boundary objects."""

    trough_offset: int
    episode_first_offset: int
    loss_start_date: Any
    trough_date: Any
    recovery_date: Any | None
    portfolio_loss: float
    asset_loss_contributions: dict[Symbol, float]
    transaction_cost_loss_contribution: float


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rolling_compounded(values: Any) -> Any:
    windows = np.lib.stride_tricks.sliding_window_view(values, ROLLING_WINDOW_ROWS)
    return np.expm1(np.log1p(windows).sum(axis=1))


def _candidate_frame_for_length(
    earned_returns: pd.Series,
    failure_rules: Sequence[FailureRule],
    window_rows: int,
) -> pd.DataFrame:
    """Calculate primitive ranking arrays for one configured length."""
    candidate_columns = (
        "scenario_id",
        "window_rows",
        "start_position",
        "end_position",
        "breach_count",
        "maximum_normalized_excess",
        "total_normalized_excess",
        "worst_portfolio_loss",
    )
    if len(earned_returns.index) < window_rows:
        return pd.DataFrame(columns=candidate_columns)

    values = earned_returns.to_numpy(dtype=np.float64, copy=False)
    return_windows = np.lib.stride_tricks.sliding_window_view(values, window_rows)
    wealth = np.cumprod(1.0 + return_windows, axis=1)
    running_high = np.maximum.accumulate(np.maximum(wealth, 1.0), axis=1)
    maximum_drawdowns = (1.0 - wealth / running_high).max(axis=1)
    total_returns = wealth[:, -1] - 1.0

    rolling_returns = _rolling_compounded(values)
    rolling_window_values = np.lib.stride_tricks.sliding_window_view(
        rolling_returns,
        window_rows - ROLLING_WINDOW_ROWS + 1,
    )
    worst_rolling_returns = rolling_window_values.min(axis=1)

    rolling_volatility = (
        np.lib.stride_tricks.sliding_window_view(values, ROLLING_WINDOW_ROWS).std(
            axis=1,
            ddof=1,
        )
        * math.sqrt(252.0)
    )
    volatility_evaluable = np.lib.stride_tricks.sliding_window_view(
        rolling_volatility > 0.0,
        window_rows - ROLLING_WINDOW_ROWS + 1,
    ).any(axis=1)

    normalized_excesses: list[Any] = []
    for rule in failure_rules:
        if rule.family is FailureRuleFamily.MAXIMUM_DRAWDOWN:
            observed = maximum_drawdowns
            breached = observed > rule.threshold
        elif rule.family is FailureRuleFamily.ROLLING_20_DAY_LOSS:
            observed = -worst_rolling_returns
            breached = worst_rolling_returns < -rule.threshold
        else:
            observed = np.ones_like(maximum_drawdowns)
            breached = volatility_evaluable & (observed > rule.threshold)
        normalized_excesses.append(
            np.where(breached, observed / rule.threshold - 1.0, 0.0)
        )

    excess_matrix = np.column_stack(normalized_excesses)
    breach_counts = np.count_nonzero(excess_matrix > 0.0, axis=1)
    failing = breach_counts > 0
    if not failing.any():
        return pd.DataFrame(columns=candidate_columns)

    maximum_excess = excess_matrix.max(axis=1)
    total_excess = excess_matrix.sum(axis=1)
    worst_losses = np.maximum.reduce(
        (
            maximum_drawdowns,
            np.maximum(-total_returns, 0.0),
            np.maximum(-worst_rolling_returns, 0.0),
        )
    )
    start_positions = np.arange(len(return_windows), dtype=np.int64)[failing]
    end_positions = start_positions + window_rows - 1
    start_text = earned_returns.index[start_positions].strftime("%Y%m%d")
    end_text = earned_returns.index[end_positions].strftime("%Y%m%d")
    scenario_ids = (
        "historical-"
        + f"{window_rows:03d}-"
        + start_text
        + "-"
        + end_text
    )
    return pd.DataFrame(
        {
            "scenario_id": scenario_ids,
            "window_rows": window_rows,
            "start_position": start_positions,
            "end_position": end_positions,
            "breach_count": breach_counts[failing],
            "maximum_normalized_excess": maximum_excess[failing],
            "total_normalized_excess": total_excess[failing],
            "worst_portfolio_loss": worst_losses[failing],
        }
    )


def _rank_and_deduplicate(candidates: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """Keep the strongest member of each transitive overlap episode."""
    chronological = candidates.sort_values(
        ["start_position", "end_position", "scenario_id"],
        kind="mergesort",
    ).copy()
    prior_maximum_end = chronological["end_position"].cummax().shift(1, fill_value=-1)
    chronological["overlap_group"] = (
        chronological["start_position"].gt(prior_maximum_end).cumsum()
    )
    overlap_groups = chronological.set_index("scenario_id")["overlap_group"]

    ranking_columns = (
        "breach_count",
        "maximum_normalized_excess",
        "total_normalized_excess",
        "worst_portfolio_loss",
        "scenario_id",
    )
    ranked = candidates.sort_values(
        list(ranking_columns),
        ascending=[False, False, False, False, True],
        kind="mergesort",
    ).copy()
    ranked["overlap_group"] = ranked["scenario_id"].map(overlap_groups)
    selected = ranked.drop_duplicates("overlap_group", keep="first").head(top_n)
    return selected.reset_index(drop=True)


def _window_state(values: Any) -> tuple[Any, Any]:
    wealth = np.cumprod(1.0 + values)
    running_high = np.maximum.accumulate(np.maximum(wealth, 1.0))
    return wealth, 1.0 - wealth / running_high


def _loss_episode(
    baseline: BacktestResult,
    start_position: int,
    end_position: int,
    numeric_tolerance: float,
) -> _LossEpisode:
    earned_returns = baseline.portfolio_returns.iloc[1:]
    window_values = earned_returns.iloc[start_position : end_position + 1].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    wealth, drawdowns = _window_state(window_values)
    trough_offset = int(np.argmax(drawdowns))
    peak_value = float(max(1.0, wealth[: trough_offset + 1].max()))
    observed_peaks = np.flatnonzero(wealth[: trough_offset + 1] == peak_value)
    if observed_peaks.size:
        peak_offset = int(observed_peaks[-1])
        episode_first_offset = peak_offset + 1
        loss_start_date = earned_returns.index[start_position + peak_offset].date()
    else:
        episode_first_offset = 0
        loss_start_date = earned_returns.index[start_position].date()

    trough_position = start_position + trough_offset
    trough_date = earned_returns.index[trough_position].date()
    portfolio_loss = float(drawdowns[trough_offset])

    continuation = np.cumprod(
        1.0
        + earned_returns.iloc[start_position:].to_numpy(dtype=np.float64, copy=False)
    )
    after_trough = np.arange(len(continuation)) > trough_offset
    recovered = np.flatnonzero(
        after_trough & (continuation >= peak_value - numeric_tolerance)
    )
    recovery_date = (
        earned_returns.index[start_position + int(recovered[0])].date()
        if recovered.size
        else None
    )
    if portfolio_loss == 0.0:
        recovery_date = trough_date

    episode_start = start_position + episode_first_offset
    result_start = episode_start + 1
    result_stop = trough_position + 2
    episode_asset_contributions = baseline.asset_contributions.iloc[
        result_start:result_stop
    ].to_numpy(dtype=np.float64, copy=False)
    episode_costs = baseline.transaction_costs.iloc[result_start:result_stop].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    episode_returns = baseline.portfolio_returns.iloc[result_start:result_stop].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    previous_wealth = np.concatenate(
        (np.ones(1, dtype=np.float64), np.cumprod(1.0 + episode_returns[:-1]))
    )
    linked_assets = (previous_wealth[:, None] * episode_asset_contributions).sum(axis=0)
    linked_cost = float((previous_wealth * episode_costs).sum())
    asset_loss_contributions = {
        Symbol(label): float(-linked_assets[position])
        for position, label in enumerate(baseline.asset_contributions.columns)
    }
    return _LossEpisode(
        trough_offset=trough_offset,
        episode_first_offset=episode_first_offset,
        loss_start_date=loss_start_date,
        trough_date=trough_date,
        recovery_date=recovery_date,
        portfolio_loss=portfolio_loss,
        asset_loss_contributions=asset_loss_contributions,
        transaction_cost_loss_contribution=linked_cost,
    )


def _affected_symbols(
    baseline: BacktestResult,
    start_position: int,
    end_position: int,
    numeric_tolerance: float,
) -> tuple[Symbol, ...]:
    weights = baseline.effective_weights.iloc[start_position + 1 : end_position + 2]
    active = weights.abs().gt(numeric_tolerance).any(axis="index")
    symbols = tuple(Symbol(label) for label in active.index[active].tolist())
    if not symbols:
        raise HistoricalScanValidationError("a historical breach has no affected positions")
    return symbols


def _failure_breaches(
    baseline: BacktestResult,
    failure_rules: Sequence[FailureRule],
    start_position: int,
    end_position: int,
    numeric_tolerance: float,
    loss_episode: _LossEpisode,
) -> tuple[FailureBreach, ...]:
    earned_returns = baseline.portfolio_returns.iloc[1:]
    dates = earned_returns.index[start_position : end_position + 1]
    values = earned_returns.iloc[start_position : end_position + 1].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    _, drawdowns = _window_state(values)
    rolling_returns = _rolling_compounded(values)
    rolling_volatility = (
        np.lib.stride_tricks.sliding_window_view(values, ROLLING_WINDOW_ROWS).std(
            axis=1,
            ddof=1,
        )
        * math.sqrt(252.0)
    )

    breaches: list[FailureBreach] = []
    for rule in failure_rules:
        if rule.family is FailureRuleFamily.MAXIMUM_DRAWDOWN:
            violating = np.flatnonzero(drawdowns > rule.threshold)
            if not violating.size:
                continue
            onset_offset = int(violating[0])
            worst_end_offset = loss_episode.trough_offset
            worst_start_offset = loss_episode.episode_first_offset
            observed = loss_episode.portfolio_loss
            worst_start_date = loss_episode.loss_start_date
        elif rule.family is FailureRuleFamily.ROLLING_20_DAY_LOSS:
            violating = np.flatnonzero(rolling_returns < -rule.threshold)
            if not violating.size:
                continue
            onset_offset = int(violating[0]) + ROLLING_WINDOW_ROWS - 1
            worst_rolling_offset = int(np.argmin(rolling_returns))
            worst_start_offset = worst_rolling_offset
            worst_end_offset = worst_rolling_offset + ROLLING_WINDOW_ROWS - 1
            observed = float(-rolling_returns[worst_rolling_offset])
            worst_start_date = dates[worst_start_offset].date()
        else:
            evaluable = rolling_volatility > 0.0
            violating = np.flatnonzero(evaluable & (1.0 > rule.threshold))
            if not violating.size:
                continue
            first_evaluable = int(violating[0])
            onset_offset = first_evaluable + ROLLING_WINDOW_ROWS - 1
            worst_start_offset = first_evaluable
            worst_end_offset = onset_offset
            observed = 1.0
            worst_start_date = dates[worst_start_offset].date()

        affected = _affected_symbols(
            baseline,
            start_position + worst_start_offset,
            start_position + worst_end_offset,
            numeric_tolerance,
        )
        breaches.append(
            FailureBreach(
                rule_id=rule.rule_id,
                family=rule.family,
                observed_value=float(observed),
                threshold=float(rule.threshold),
                normalized_excess=float(observed / rule.threshold - 1.0),
                onset_date=dates[onset_offset].date(),
                worst_window_start=worst_start_date,
                worst_window_end=dates[worst_end_offset].date(),
                affected_symbols=affected,
            )
        )
    return tuple(breaches)


def _metrics(values: Any) -> MetricSet:
    wealth, drawdowns = _window_state(values)
    rolling_returns = _rolling_compounded(values)
    return MetricSet(
        total_return=float(wealth[-1] - 1.0),
        maximum_drawdown=float(drawdowns.max()),
        worst_rolling_20_day_return=float(rolling_returns.min()),
        annualized_volatility=float(values.std(ddof=1) * math.sqrt(252.0)),
        observation_count=len(values),
    )


def _correlation(asset_values: Any) -> float | None:
    centered = asset_values - asset_values.mean(axis=0)
    norms = np.sqrt((centered * centered).sum(axis=0))
    if (norms == 0.0).any():
        return None
    if np.array_equal(centered[:, 0], centered[:, 1]):
        return 1.0
    if np.array_equal(centered[:, 0], -centered[:, 1]):
        return -1.0
    correlation = float((centered[:, 0] * centered[:, 1]).sum() / (norms[0] * norms[1]))
    if not -1.0 <= correlation <= 1.0:
        raise HistoricalScanValidationError("calculated SPY-TLT correlation is outside [-1, 1]")
    return correlation


def _window_evidence(
    baseline: BacktestResult,
    start_position: int,
    end_position: int,
    breaches: tuple[FailureBreach, ...],
    loss_episode: _LossEpisode,
) -> HistoricalWindowEvidence:
    earned_index = baseline.portfolio_returns.index[1:]
    result_start = start_position + 1
    result_stop = end_position + 2
    asset_returns = baseline.asset_returns.iloc[result_start:result_stop].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    asset_total_returns = np.expm1(np.log1p(asset_returns).sum(axis=0))
    asset_volatilities = asset_returns.std(axis=0, ddof=1) * math.sqrt(252.0)
    symbols = tuple(Symbol(label) for label in baseline.asset_returns.columns)
    return HistoricalWindowEvidence(
        window_rows=cast(Literal[20, 60, 126], end_position - start_position + 1),
        start_date=earned_index[start_position].date(),
        end_date=earned_index[end_position].date(),
        breach_onset_date=min(breach.onset_date for breach in breaches),
        loss_start_date=loss_episode.loss_start_date,
        trough_date=loss_episode.trough_date,
        recovery_date=loss_episode.recovery_date,
        asset_returns={
            symbol: float(asset_total_returns[position])
            for position, symbol in enumerate(symbols)
        },
        asset_realized_volatilities={
            symbol: float(asset_volatilities[position])
            for position, symbol in enumerate(symbols)
        },
        spy_tlt_correlation=_correlation(asset_returns),
        total_turnover=float(baseline.turnover.iloc[result_start:result_stop].sum()),
        total_transaction_cost=float(
            baseline.transaction_costs.iloc[result_start:result_stop].sum()
        ),
        portfolio_loss_to_trough=loss_episode.portfolio_loss,
        asset_loss_contributions=loss_episode.asset_loss_contributions,
        transaction_cost_loss_contribution=(
            loss_episode.transaction_cost_loss_contribution
        ),
    )


def _build_result(
    selected: dict[str, Any],
    rank: int,
    dataset: StoredDataset,
    strategy: Strategy,
    experiment: ExperimentSpec,
    baseline: BacktestResult,
    config_sha256: str,
) -> StressResult:
    start_position = int(selected["start_position"])
    end_position = int(selected["end_position"])
    values = baseline.portfolio_returns.iloc[start_position + 1 : end_position + 2].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    loss_episode = _loss_episode(
        baseline,
        start_position,
        end_position,
        experiment.numeric_tolerance,
    )
    breaches = _failure_breaches(
        baseline,
        experiment.failure_rules,
        start_position,
        end_position,
        experiment.numeric_tolerance,
        loss_episode,
    )
    if len(breaches) != int(selected["breach_count"]):
        raise HistoricalScanValidationError("selected breach count changed during evidence build")
    maximum_excess = max(breach.normalized_excess for breach in breaches)
    total_excess = math.fsum(breach.normalized_excess for breach in breaches)
    scenario_id = str(selected["scenario_id"])
    scenario_input = {
        "end_date": baseline.portfolio_returns.index[end_position + 1].date().isoformat(),
        "family": "historical_window",
        "scenario_id": scenario_id,
        "start_date": baseline.portfolio_returns.index[start_position + 1].date().isoformat(),
        "window_rows": int(selected["window_rows"]),
    }
    return StressResult(
        experiment_id=experiment.experiment_id,
        scenario_id=scenario_id,
        dataset_id=dataset.manifest.dataset_id,
        strategy_id=strategy.spec.strategy_id,
        input_sha256=_canonical_sha256(scenario_input),
        config_sha256=config_sha256,
        data_sha256=dataset.manifest.sha256,
        code_version=experiment.code_version,
        engine_version=HISTORICAL_SCANNER_VERSION,
        status=ResultStatus.VALID,
        metrics=_metrics(values),
        historical_window=_window_evidence(
            baseline,
            start_position,
            end_position,
            breaches,
            loss_episode,
        ),
        breaches=breaches,
        breach_count=len(breaches),
        maximum_normalized_excess=maximum_excess,
        total_normalized_excess=total_excess,
        worst_portfolio_loss=float(selected["worst_portfolio_loss"]),
        rank=rank,
    )


def scan_historical_failures(
    dataset: StoredDataset,
    strategy: Strategy,
    experiment: ExperimentSpec,
) -> tuple[StressResult, ...]:
    """Return a bounded, ranked, non-overlapping set of historical failures.

    The engine runs the strategy once. Candidate metrics use rolling array views;
    typed evidence objects are constructed only for the selected ``top_k`` rows.
    """
    if experiment.dataset_id != dataset.manifest.dataset_id:
        raise HistoricalScanValidationError("ExperimentSpec dataset_id does not match the dataset")
    if experiment.data_sha256 != dataset.manifest.sha256:
        raise HistoricalScanValidationError("ExperimentSpec data_sha256 does not match the dataset")
    if experiment.strategy != strategy.spec:
        raise HistoricalScanValidationError("strategy does not match ExperimentSpec.strategy")

    baseline = run_backtest(
        dataset,
        strategy,
        experiment.transaction_cost_bps,
        experiment.numeric_tolerance,
    )
    earned_returns = baseline.portfolio_returns.iloc[1:]
    candidate_frames = tuple(
        _candidate_frame_for_length(
            earned_returns,
            experiment.failure_rules,
            window_rows,
        )
        for window_rows in experiment.historical_window_rows
    )
    candidates = pd.concat(candidate_frames, ignore_index=True)
    if candidates.empty:
        return ()

    selected = _rank_and_deduplicate(candidates, experiment.top_k)
    selected_records = selected.to_dict(orient="records")
    config_sha256 = _canonical_sha256(experiment.model_dump(mode="json"))
    return tuple(
        _build_result(
            record,
            rank,
            dataset,
            strategy,
            experiment,
            baseline,
            config_sha256,
        )
        for rank, record in enumerate(selected_records, start=1)
    )
