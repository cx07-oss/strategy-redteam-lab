"""Pure deterministic transforms for bounded synthetic stress scenarios.

Narrative fields are never inspected here. A component's validated numeric fields
are its complete executable meaning. Every market transform returns a new frame,
and composite execution stages all changes privately before returning evidence.
"""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

import pandas as pd  # type: ignore[import-untyped]
from pandas.api.types import is_bool_dtype, is_numeric_dtype  # type: ignore[import-untyped]

from strategy_redteam.backtest import (
    BacktestResult,
    run_backtest,
    run_backtest_with_asset_returns,
)
from strategy_redteam.data import StoredDataset
from strategy_redteam.domain import (
    DEFAULT_NUMERIC_TOLERANCE,
    AssetReturnSummary,
    ComponentTransformSummary,
    FailureRule,
    ReturnSummary,
    StressComponent,
    StressFamily,
    StressScenario,
    Symbol,
)
from strategy_redteam.strategy import Strategy

if TYPE_CHECKING:
    np: Any
else:
    import numpy as np

STRESS_TRANSFORM_VERSION = "stress-transform-1.0"
_MAX_SEED = 4_294_967_295
_MAX_TRANSACTION_COST_BPS = 10_000.0


class StressTransformError(Exception):
    """Base class for typed deterministic stress-transform failures."""


class StressValidationError(StressTransformError):
    """The return frame or scenario cannot satisfy the transform contract."""


class StressWindowError(StressValidationError):
    """A component window is absent, too short, or outside the evaluation window."""


class StressArithmeticError(StressTransformError):
    """A transform would create a non-finite or impossible arithmetic return."""


class StressCorrelationError(StressValidationError):
    """A source or target correlation matrix is invalid or ill-conditioned."""


@dataclass(frozen=True)
class StressTransformResult:
    """Atomic evidence from one ordered synthetic scenario application."""

    scenario: StressScenario
    seed: int
    numeric_tolerance: float
    component_order: tuple[StressFamily, ...]
    baseline_asset_returns: pd.DataFrame
    stressed_asset_returns: pd.DataFrame
    transaction_cost_bps_before: float
    transaction_cost_bps_after: float
    component_summaries: tuple[ComponentTransformSummary, ...]
    pre_transform_summary: ReturnSummary
    post_transform_summary: ReturnSummary

    def __post_init__(self) -> None:
        """Enforce matching scenario windows and complete ordered component audits."""
        _validate_numeric_tolerance(self.numeric_tolerance)
        before = self.pre_transform_summary
        after = self.post_transform_summary
        if (
            before.start_date != after.start_date
            or before.end_date != after.end_date
            or before.row_count != after.row_count
        ):
            raise StressValidationError(
                "scenario pre/post summaries must use an identical window"
            )
        summary_order = tuple(summary.family for summary in self.component_summaries)
        if summary_order != self.component_order:
            raise StressValidationError(
                "component summaries must exactly match the declared transform order"
            )

    def canonical_bytes(self) -> bytes:
        """Serialize exact ordered inputs and outputs into repeatable evidence bytes."""
        metadata = {
            "component_order": [family.value for family in self.component_order],
            "component_summaries": [
                summary.model_dump(mode="json") for summary in self.component_summaries
            ],
            "post_transform_summary": self.post_transform_summary.model_dump(mode="json"),
            "pre_transform_summary": self.pre_transform_summary.model_dump(mode="json"),
            "scenario": self.scenario.model_dump(mode="json"),
            "seed": self.seed,
            "numeric_tolerance": self.numeric_tolerance.hex(),
            "transaction_cost_bps_after": self.transaction_cost_bps_after.hex(),
            "transaction_cost_bps_before": self.transaction_cost_bps_before.hex(),
            "transform_version": STRESS_TRANSFORM_VERSION,
        }
        header = json.dumps(
            metadata,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        baseline = _canonical_return_frame_bytes(self.baseline_asset_returns)
        stressed = _canonical_return_frame_bytes(self.stressed_asset_returns)
        return b"".join(
            (
                struct.pack("<Q", len(header)),
                header,
                struct.pack("<Q", len(baseline)),
                baseline,
                stressed,
            )
        )

    def canonical_sha256(self) -> str:
        """Hash the byte-equivalent deterministic result."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class StressedBacktestResult:
    """Baseline, atomic transform evidence, and complete stressed-path replay."""

    baseline_backtest: BacktestResult
    transform: StressTransformResult
    stressed_backtest: BacktestResult


def _validate_numeric_tolerance(numeric_tolerance: float) -> None:
    if (
        isinstance(numeric_tolerance, bool)
        or not isinstance(numeric_tolerance, (int, float))
        or not np.isfinite(numeric_tolerance)
        or not 0.0 < numeric_tolerance < 1.0
    ):
        raise StressValidationError("numeric_tolerance must be finite and in (0, 1)")


def _validate_return_frame(asset_returns: pd.DataFrame) -> None:
    if not isinstance(asset_returns, pd.DataFrame):
        raise StressValidationError("asset_returns must be a pandas DataFrame")
    if asset_returns.empty:
        raise StressValidationError("asset_returns must contain at least one row")
    if not isinstance(asset_returns.index, pd.DatetimeIndex):
        raise StressValidationError("asset_returns must use a DatetimeIndex")
    if str(asset_returns.index.tz).upper() != "UTC":
        raise StressValidationError("asset_returns dates must be timezone-aware UTC")
    if not asset_returns.index.equals(asset_returns.index.normalize()):
        raise StressValidationError("asset_returns dates must be normalized market dates")
    if not asset_returns.index.is_unique or not asset_returns.index.is_monotonic_increasing:
        raise StressValidationError("asset_returns dates must be unique and strictly increasing")
    if not asset_returns.columns.is_unique:
        raise StressValidationError("asset_returns columns must be unique")

    column_text = tuple(str(label) for label in asset_returns.columns)
    try:
        symbols = tuple(Symbol(label) for label in column_text)
    except ValueError as exc:
        raise StressValidationError("asset_returns contains an unsupported asset") from exc
    if len(symbols) != 2 or set(symbols) != {Symbol.SPY, Symbol.TLT}:
        raise StressValidationError("asset_returns must contain exactly SPY and TLT")
    if any(
        is_bool_dtype(dtype) or not is_numeric_dtype(dtype)
        for dtype in asset_returns.dtypes
    ):
        raise StressValidationError("asset_returns values must be numeric")

    values = asset_returns.to_numpy(dtype=np.float64, copy=False)
    if not np.isfinite(values).all():
        raise StressValidationError("asset_returns values must be finite")
    if (values <= -1.0).any():
        raise StressArithmeticError("asset_returns must be strictly greater than -1")


def _canonical_return_frame_bytes(asset_returns: pd.DataFrame) -> bytes:
    _validate_return_frame(asset_returns)
    header = json.dumps(
        {
            "columns": [str(label) for label in asset_returns.columns],
            "columns_name": (
                None if asset_returns.columns.name is None else str(asset_returns.columns.name)
            ),
            "index_name": (
                None if asset_returns.index.name is None else str(asset_returns.index.name)
            ),
            "index_timezone": str(asset_returns.index.tz),
            "shape": asset_returns.shape,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    index_bytes = asset_returns.index.asi8.astype("<i8", copy=False).tobytes(order="C")
    value_bytes = np.ascontiguousarray(
        asset_returns.to_numpy(dtype=np.float64, copy=False),
        dtype="<f8",
    ).tobytes(order="C")
    return b"".join(
        (
            struct.pack("<Q", len(header)),
            header,
            struct.pack("<Q", len(index_bytes)),
            index_bytes,
            value_bytes,
        )
    )


def canonical_return_frame_sha256(asset_returns: pd.DataFrame) -> str:
    """Hash exact dates, column order, labels, and float64 return values."""
    return hashlib.sha256(_canonical_return_frame_bytes(asset_returns)).hexdigest()


def _date_position(asset_returns: pd.DataFrame, value: date, field_name: str) -> int:
    timestamp = pd.Timestamp(value).tz_localize("UTC")
    try:
        location = asset_returns.index.get_loc(timestamp)
    except KeyError as exc:
        raise StressWindowError(f"{field_name} must be an observed market date") from exc
    if not isinstance(location, (int, np.integer)):
        raise StressWindowError(f"{field_name} must identify exactly one market row")
    return int(location)


def _window_positions(
    asset_returns: pd.DataFrame,
    start_date: date,
    end_date: date,
    *,
    minimum_rows: int,
) -> tuple[int, int]:
    start_position = _date_position(asset_returns, start_date, "start_date")
    end_position = _date_position(asset_returns, end_date, "end_date")
    row_count = end_position - start_position + 1
    if row_count < minimum_rows:
        raise StressWindowError(
            f"component window requires at least {minimum_rows} observed market rows"
        )
    return start_position, end_position


def _symbol_positions(
    asset_returns: pd.DataFrame,
    symbols: tuple[Symbol, ...],
) -> tuple[int, ...]:
    positions: list[int] = []
    for symbol in symbols:
        try:
            location = asset_returns.columns.get_loc(symbol.value)
        except KeyError as exc:
            raise StressValidationError(f"unsupported or absent asset: {symbol.value}") from exc
        if not isinstance(location, (int, np.integer)):
            raise StressValidationError(f"asset column must be unique: {symbol.value}")
        positions.append(int(location))
    return tuple(positions)


def _validate_transformed_values(values: Any, operation: str) -> None:
    if not np.isfinite(values).all():
        raise StressArithmeticError(f"{operation} produced a non-finite return")
    if (values <= -1.0).any():
        raise StressArithmeticError(f"{operation} produced a return at or below -1")


def _require_family(component: StressComponent, family: StressFamily) -> None:
    if component.family is not family:
        raise StressValidationError(f"component family must be {family.value}")


def apply_one_day_gap(
    asset_returns: pd.DataFrame,
    component: StressComponent,
) -> pd.DataFrame:
    """Multiply observed gross returns by explicit one-day shock gross returns."""
    _require_family(component, StressFamily.ONE_DAY_GAP)
    _validate_return_frame(asset_returns)
    if component.date is None or component.shocks is None:
        raise StressValidationError("one_day_gap numeric fields are incomplete")

    row_position = _date_position(asset_returns, component.date, "date")
    symbols = tuple(component.shocks)
    column_positions = _symbol_positions(asset_returns, symbols)
    observed = asset_returns.iloc[row_position, list(column_positions)].to_numpy(
        dtype=np.float64,
        copy=True,
    )
    shocks = np.asarray([component.shocks[symbol] for symbol in symbols], dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        transformed = np.expm1(np.log1p(observed) + np.log1p(shocks))
    _validate_transformed_values(transformed, component.family.value)

    output = asset_returns.copy(deep=True)
    output.iloc[row_position, list(column_positions)] = transformed
    return output


def apply_sustained_cumulative_shock(
    asset_returns: pd.DataFrame,
    component: StressComponent,
) -> pd.DataFrame:
    """Distribute each cumulative shock as one constant daily log increment."""
    _require_family(component, StressFamily.SUSTAINED_CUMULATIVE_SHOCK)
    _validate_return_frame(asset_returns)
    if (
        component.start_date is None
        or component.duration_rows is None
        or component.shocks is None
    ):
        raise StressValidationError("sustained_cumulative_shock numeric fields are incomplete")

    start_position = _date_position(asset_returns, component.start_date, "start_date")
    stop_position = start_position + component.duration_rows
    if stop_position > len(asset_returns.index):
        raise StressWindowError("sustained shock duration exceeds available market rows")
    symbols = tuple(component.shocks)
    column_positions = _symbol_positions(asset_returns, symbols)
    observed = asset_returns.iloc[
        start_position:stop_position,
        list(column_positions),
    ].to_numpy(dtype=np.float64, copy=True)
    increments = np.asarray(
        [np.log1p(component.shocks[symbol]) / component.duration_rows for symbol in symbols],
        dtype=np.float64,
    )
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        transformed = np.expm1(np.log1p(observed) + increments)
    _validate_transformed_values(transformed, component.family.value)

    output = asset_returns.copy(deep=True)
    output.iloc[start_position:stop_position, list(column_positions)] = transformed
    return output


def apply_volatility_multiplier(
    asset_returns: pd.DataFrame,
    component: StressComponent,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
) -> pd.DataFrame:
    """Scale demeaned log-return innovations and restore each staged mean."""
    _require_family(component, StressFamily.VOLATILITY_MULTIPLIER)
    _validate_numeric_tolerance(numeric_tolerance)
    _validate_return_frame(asset_returns)
    if (
        component.start_date is None
        or component.end_date is None
        or component.symbols is None
        or component.volatility_multiplier is None
    ):
        raise StressValidationError("volatility_multiplier numeric fields are incomplete")

    start_position, end_position = _window_positions(
        asset_returns,
        component.start_date,
        component.end_date,
        minimum_rows=2,
    )
    column_positions = _symbol_positions(asset_returns, component.symbols)
    observed = asset_returns.iloc[
        start_position : end_position + 1,
        list(column_positions),
    ].to_numpy(dtype=np.float64, copy=True)
    log_returns = np.log1p(observed)
    means = log_returns.mean(axis=0)
    innovations = log_returns - means
    source_variance = innovations.var(axis=0, ddof=1)
    if (source_variance <= numeric_tolerance).any():
        raise StressValidationError("volatility source window has zero variance")
    transformed_logs = means + component.volatility_multiplier * innovations
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        transformed = np.expm1(transformed_logs)
    _validate_transformed_values(transformed, component.family.value)

    output = asset_returns.copy(deep=True)
    output.iloc[
        start_position : end_position + 1,
        list(column_positions),
    ] = transformed
    return output


def _correlation_eigendecomposition(
    matrix: Any,
    name: str,
    numeric_tolerance: float,
) -> tuple[Any, Any]:
    if matrix.shape != (2, 2) or not np.isfinite(matrix).all():
        raise StressCorrelationError(f"{name} correlation matrix must be finite and 2x2")
    if not np.allclose(matrix, matrix.T, rtol=0.0, atol=numeric_tolerance):
        raise StressCorrelationError(f"{name} correlation matrix must be symmetric")
    if not np.allclose(np.diag(matrix), 1.0, rtol=0.0, atol=numeric_tolerance):
        raise StressCorrelationError(f"{name} correlation matrix must have a unit diagonal")

    symmetric = (matrix + matrix.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    if (eigenvalues < -numeric_tolerance).any():
        raise StressCorrelationError(
            f"{name} correlation matrix is not positive semidefinite"
        )
    if (eigenvalues <= numeric_tolerance).any():
        raise StressCorrelationError(
            f"{name} correlation matrix is singular or ill-conditioned"
        )
    condition_number = float(eigenvalues.max() / eigenvalues.min())
    if not np.isfinite(condition_number) or condition_number >= 1.0 / numeric_tolerance:
        raise StressCorrelationError(
            f"{name} correlation matrix is singular or ill-conditioned"
        )
    return eigenvalues, eigenvectors


def apply_correlation_target(
    asset_returns: pd.DataFrame,
    component: StressComponent,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
) -> pd.DataFrame:
    """Symmetrically whiten and color standardized SPY/TLT log innovations."""
    _require_family(component, StressFamily.CORRELATION_TARGET)
    _validate_numeric_tolerance(numeric_tolerance)
    _validate_return_frame(asset_returns)
    if (
        component.start_date is None
        or component.end_date is None
        or component.target_correlation is None
    ):
        raise StressValidationError("correlation_target numeric fields are incomplete")

    start_position, end_position = _window_positions(
        asset_returns,
        component.start_date,
        component.end_date,
        minimum_rows=3,
    )
    symbols = tuple(Symbol(str(label)) for label in asset_returns.columns)
    column_positions = _symbol_positions(asset_returns, symbols)
    observed = asset_returns.iloc[
        start_position : end_position + 1,
        list(column_positions),
    ].to_numpy(dtype=np.float64, copy=True)
    log_returns = np.log1p(observed)
    means = log_returns.mean(axis=0)
    innovations = log_returns - means
    source_variance = innovations.var(axis=0, ddof=1)
    if (source_variance <= numeric_tolerance).any():
        raise StressCorrelationError("correlation source window has zero variance")
    source_std = np.sqrt(source_variance)

    standardized = innovations / source_std
    observed_correlation = standardized.T @ standardized / (len(standardized) - 1)
    observed_values, observed_vectors = _correlation_eigendecomposition(
        observed_correlation,
        "source",
        numeric_tolerance,
    )
    target = np.asarray(
        [
            [1.0, component.target_correlation],
            [component.target_correlation, 1.0],
        ],
        dtype=np.float64,
    )
    target_values, target_vectors = _correlation_eigendecomposition(
        target,
        "target",
        numeric_tolerance,
    )

    inverse_source_root = (
        observed_vectors * (1.0 / np.sqrt(observed_values))
    ) @ observed_vectors.T
    target_root = (target_vectors * np.sqrt(target_values)) @ target_vectors.T
    colored = standardized @ inverse_source_root @ target_root
    transformed_logs = means + colored * source_std
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        transformed = np.expm1(transformed_logs)
    _validate_transformed_values(transformed, component.family.value)
    realized_correlation = float(np.corrcoef(transformed_logs, rowvar=False)[0, 1])
    if abs(realized_correlation - component.target_correlation) > numeric_tolerance:
        raise StressCorrelationError("target correlation was not realized within tolerance")

    output = asset_returns.copy(deep=True)
    output.iloc[
        start_position : end_position + 1,
        list(column_positions),
    ] = transformed
    return output


def apply_transaction_cost_multiplier(
    transaction_cost_bps: float,
    component: StressComponent,
) -> float:
    """Scale execution costs without reading or modifying market-return data."""
    _require_family(component, StressFamily.TRANSACTION_COST_MULTIPLIER)
    if (
        isinstance(transaction_cost_bps, bool)
        or not isinstance(transaction_cost_bps, (int, float))
        or not np.isfinite(transaction_cost_bps)
        or not 0.0 <= transaction_cost_bps < _MAX_TRANSACTION_COST_BPS
    ):
        raise StressValidationError("transaction_cost_bps must be finite in [0, 10000)")
    if component.transaction_cost_multiplier is None:
        raise StressValidationError("transaction_cost_multiplier numeric field is incomplete")
    transformed = float(transaction_cost_bps * component.transaction_cost_multiplier)
    if not np.isfinite(transformed) or not 0.0 <= transformed < _MAX_TRANSACTION_COST_BPS:
        raise StressArithmeticError(
            "transaction_cost_multiplier produced an invalid execution-cost assumption"
        )
    return transformed


def _summarize_returns(
    asset_returns: pd.DataFrame,
    start_position: int,
    end_position: int,
    numeric_tolerance: float,
) -> ReturnSummary:
    window = asset_returns.iloc[start_position : end_position + 1]
    values = window.to_numpy(dtype=np.float64, copy=False)
    log_returns = np.log1p(values)
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        cumulative_returns = np.expm1(log_returns.sum(axis=0))
    _validate_transformed_values(cumulative_returns, "return summary")
    means = log_returns.mean(axis=0)
    sample_variance = (
        log_returns.var(axis=0, ddof=1)
        if len(window.index) > 1
        else np.full(len(window.columns), np.nan, dtype=np.float64)
    )
    sample_std = np.sqrt(sample_variance)

    correlation: float | None = None
    if len(window.index) > 1 and (sample_variance > numeric_tolerance).all():
        correlation = float(np.corrcoef(log_returns, rowvar=False)[0, 1])
        if not np.isfinite(correlation) or not -1.0 <= correlation <= 1.0:
            raise StressArithmeticError("return summary correlation is invalid")

    assets = tuple(
        AssetReturnSummary(
            symbol=Symbol(str(label)),
            cumulative_simple_return=float(cumulative_returns[position]),
            mean_log_return=float(means[position]),
            sample_log_return_std=(
                None if np.isnan(sample_std[position]) else float(sample_std[position])
            ),
        )
        for position, label in enumerate(window.columns)
    )
    return ReturnSummary(
        start_date=window.index[0].date(),
        end_date=window.index[-1].date(),
        row_count=len(window.index),
        assets=assets,
        spy_tlt_correlation=correlation,
    )


def summarize_asset_returns(
    asset_returns: pd.DataFrame,
    numeric_tolerance: float,
) -> ReturnSummary:
    """Return one compact deterministic summary without exposing the daily path."""
    _validate_numeric_tolerance(numeric_tolerance)
    _validate_return_frame(asset_returns)
    return _summarize_returns(
        asset_returns,
        0,
        len(asset_returns.index) - 1,
        numeric_tolerance,
    )


def _preflight_scenario(
    asset_returns: pd.DataFrame,
    scenario: StressScenario,
    transaction_cost_bps: float,
    numeric_tolerance: float,
) -> tuple[int, int]:
    evaluation_start = _date_position(
        asset_returns,
        scenario.evaluation_start,
        "evaluation_start",
    )
    evaluation_end = _date_position(
        asset_returns,
        scenario.evaluation_end,
        "evaluation_end",
    )
    if evaluation_start > evaluation_end:
        raise StressWindowError("evaluation window dates are reversed")

    cost_component_count = sum(
        component.family is StressFamily.TRANSACTION_COST_MULTIPLIER
        for component in scenario.components
    )
    if cost_component_count > 1:
        raise StressValidationError(
            "a scenario may contain at most one transaction cost multiplier"
        )

    staged_cost = transaction_cost_bps
    for component in scenario.components:
        if component.family is StressFamily.HISTORICAL_WINDOW:
            raise StressValidationError("historical_window is not a synthetic transform")
        if component.family is StressFamily.ONE_DAY_GAP:
            if component.date is None or component.shocks is None:
                raise StressValidationError("one_day_gap numeric fields are incomplete")
            position = _date_position(asset_returns, component.date, "date")
            _symbol_positions(asset_returns, tuple(component.shocks))
            if not evaluation_start <= position <= evaluation_end:
                raise StressWindowError("one-day gap falls outside the evaluation window")
        elif component.family is StressFamily.SUSTAINED_CUMULATIVE_SHOCK:
            if (
                component.start_date is None
                or component.duration_rows is None
                or component.shocks is None
            ):
                raise StressValidationError(
                    "sustained_cumulative_shock numeric fields are incomplete"
                )
            start = _date_position(asset_returns, component.start_date, "start_date")
            end = start + component.duration_rows - 1
            _symbol_positions(asset_returns, tuple(component.shocks))
            if end >= len(asset_returns.index):
                raise StressWindowError("sustained shock duration exceeds available market rows")
            if start < evaluation_start or end > evaluation_end:
                raise StressWindowError("sustained shock falls outside the evaluation window")
        elif component.family is StressFamily.VOLATILITY_MULTIPLIER:
            if (
                component.start_date is None
                or component.end_date is None
                or component.symbols is None
            ):
                raise StressValidationError("volatility_multiplier numeric fields are incomplete")
            start, end = _window_positions(
                asset_returns,
                component.start_date,
                component.end_date,
                minimum_rows=2,
            )
            _symbol_positions(asset_returns, component.symbols)
            if start < evaluation_start or end > evaluation_end:
                raise StressWindowError("volatility window falls outside the evaluation window")
        elif component.family is StressFamily.CORRELATION_TARGET:
            if (
                component.start_date is None
                or component.end_date is None
                or component.target_correlation is None
            ):
                raise StressValidationError("correlation_target numeric fields are incomplete")
            start, end = _window_positions(
                asset_returns,
                component.start_date,
                component.end_date,
                minimum_rows=3,
            )
            if start < evaluation_start or end > evaluation_end:
                raise StressWindowError("correlation window falls outside the evaluation window")
            target = np.asarray(
                [
                    [1.0, component.target_correlation],
                    [component.target_correlation, 1.0],
                ],
                dtype=np.float64,
            )
            _correlation_eigendecomposition(target, "target", numeric_tolerance)
        else:
            staged_cost = apply_transaction_cost_multiplier(staged_cost, component)
    return evaluation_start, evaluation_end


def _component_window_positions(
    asset_returns: pd.DataFrame,
    component: StressComponent,
    evaluation_start: int,
    evaluation_end: int,
) -> tuple[int, int]:
    if component.family is StressFamily.ONE_DAY_GAP:
        if component.date is None:
            raise StressValidationError("one_day_gap date is incomplete")
        position = _date_position(asset_returns, component.date, "date")
        return position, position
    if component.family is StressFamily.SUSTAINED_CUMULATIVE_SHOCK:
        if component.start_date is None or component.duration_rows is None:
            raise StressValidationError("sustained shock window is incomplete")
        start = _date_position(asset_returns, component.start_date, "start_date")
        return start, start + component.duration_rows - 1
    if component.family is StressFamily.VOLATILITY_MULTIPLIER:
        if component.start_date is None or component.end_date is None:
            raise StressValidationError("volatility window is incomplete")
        return _window_positions(
            asset_returns,
            component.start_date,
            component.end_date,
            minimum_rows=2,
        )
    if component.family is StressFamily.CORRELATION_TARGET:
        if component.start_date is None or component.end_date is None:
            raise StressValidationError("correlation window is incomplete")
        return _window_positions(
            asset_returns,
            component.start_date,
            component.end_date,
            minimum_rows=3,
        )
    if component.family is StressFamily.TRANSACTION_COST_MULTIPLIER:
        return evaluation_start, evaluation_end
    raise StressValidationError("historical_window is not a synthetic transform")


def _apply_component_sequence(
    asset_returns: pd.DataFrame,
    scenario: StressScenario,
    transaction_cost_bps: float,
    numeric_tolerance: float,
    evaluation_start: int,
    evaluation_end: int,
    *,
    retain_summaries: bool,
) -> tuple[pd.DataFrame, float, tuple[ComponentTransformSummary, ...]]:
    staged = asset_returns.copy(deep=True)
    staged_cost = transaction_cost_bps
    summaries: list[ComponentTransformSummary] = []
    for component_index, component in enumerate(scenario.components):
        window_start, window_end = _component_window_positions(
            staged,
            component,
            evaluation_start,
            evaluation_end,
        )
        pre_summary = (
            _summarize_returns(
                staged,
                window_start,
                window_end,
                numeric_tolerance,
            )
            if retain_summaries
            else None
        )
        cost_before = staged_cost
        if component.family is StressFamily.ONE_DAY_GAP:
            staged = apply_one_day_gap(staged, component)
        elif component.family is StressFamily.SUSTAINED_CUMULATIVE_SHOCK:
            staged = apply_sustained_cumulative_shock(staged, component)
        elif component.family is StressFamily.VOLATILITY_MULTIPLIER:
            staged = apply_volatility_multiplier(staged, component, numeric_tolerance)
        elif component.family is StressFamily.CORRELATION_TARGET:
            staged = apply_correlation_target(staged, component, numeric_tolerance)
        elif component.family is StressFamily.TRANSACTION_COST_MULTIPLIER:
            staged_cost = apply_transaction_cost_multiplier(staged_cost, component)
        else:
            raise StressValidationError("historical_window is not a synthetic transform")

        if pre_summary is not None:
            summaries.append(
                ComponentTransformSummary(
                    component_index=component_index,
                    family=component.family,
                    pre_transform_summary=pre_summary,
                    post_transform_summary=_summarize_returns(
                        staged,
                        window_start,
                        window_end,
                        numeric_tolerance,
                    ),
                    transaction_cost_bps_before=cost_before,
                    transaction_cost_bps_after=staged_cost,
                )
            )
    return staged, staged_cost, tuple(summaries)


def apply_stress_scenario(
    asset_returns: pd.DataFrame,
    scenario: StressScenario,
    transaction_cost_bps: float = 0.0,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    seed: int = 0,
) -> StressTransformResult:
    """Apply a scenario in order and return evidence only after full success.

    Each component consumes the privately staged output of its predecessor. Mean,
    variance, and correlation conventions therefore refer to the immediate
    pre-component window. No intermediate frame is persisted or exposed.
    """
    _validate_numeric_tolerance(numeric_tolerance)
    _validate_return_frame(asset_returns)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= _MAX_SEED:
        raise StressValidationError(f"seed must be an integer in [0, {_MAX_SEED}]")
    if (
        isinstance(transaction_cost_bps, bool)
        or not isinstance(transaction_cost_bps, (int, float))
        or not np.isfinite(transaction_cost_bps)
        or not 0.0 <= transaction_cost_bps < _MAX_TRANSACTION_COST_BPS
    ):
        raise StressValidationError("transaction_cost_bps must be finite in [0, 10000)")

    evaluation_start, evaluation_end = _preflight_scenario(
        asset_returns,
        scenario,
        float(transaction_cost_bps),
        numeric_tolerance,
    )
    baseline = asset_returns.copy(deep=True)
    # This private dry run completes every sequential numerical validation before
    # the evidence-producing pass starts. It cannot mutate or persist source data.
    _apply_component_sequence(
        baseline,
        scenario,
        float(transaction_cost_bps),
        numeric_tolerance,
        evaluation_start,
        evaluation_end,
        retain_summaries=False,
    )
    pre_transform_summary = _summarize_returns(
        baseline,
        evaluation_start,
        evaluation_end,
        numeric_tolerance,
    )
    staged, staged_cost, component_summaries = _apply_component_sequence(
        baseline,
        scenario,
        float(transaction_cost_bps),
        numeric_tolerance,
        evaluation_start,
        evaluation_end,
        retain_summaries=True,
    )

    post_transform_summary = _summarize_returns(
        staged,
        evaluation_start,
        evaluation_end,
        numeric_tolerance,
    )
    return StressTransformResult(
        scenario=scenario,
        seed=seed,
        numeric_tolerance=numeric_tolerance,
        component_order=tuple(component.family for component in scenario.components),
        baseline_asset_returns=baseline,
        stressed_asset_returns=staged,
        transaction_cost_bps_before=float(transaction_cost_bps),
        transaction_cost_bps_after=staged_cost,
        component_summaries=component_summaries,
        pre_transform_summary=pre_transform_summary,
        post_transform_summary=post_transform_summary,
    )


def run_stressed_backtest(
    dataset: StoredDataset,
    strategy: Strategy,
    scenario: StressScenario,
    transaction_cost_bps: float = 0.0,
    numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    failure_rules: Sequence[FailureRule] = (),
    seed: int = 0,
) -> StressedBacktestResult:
    """Transform returns, then evaluate the complete stressed dataset path.

    Scenario windows bound only the declared market transformations and summary
    comparisons. Strategy drift, costs, failure onset, trough, and recovery are
    evaluated over every dataset row so effects after the scenario window remain.
    """
    baseline = run_backtest(
        dataset,
        strategy,
        transaction_cost_bps,
        numeric_tolerance,
    )
    transform = apply_stress_scenario(
        baseline.asset_returns,
        scenario,
        transaction_cost_bps,
        numeric_tolerance,
        seed,
    )
    stressed = run_backtest_with_asset_returns(
        dataset,
        strategy,
        transform.stressed_asset_returns,
        transform.transaction_cost_bps_after,
        numeric_tolerance,
        failure_rules,
        baseline.portfolio_returns,
    )
    return StressedBacktestResult(
        baseline_backtest=baseline,
        transform=transform,
        stressed_backtest=stressed,
    )
