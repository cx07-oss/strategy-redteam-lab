"""Deterministic Gate 3 strategy interfaces and built-in implementations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import pandas as pd  # type: ignore[import-untyped]

from strategy_redteam.data import StoredDataset
from strategy_redteam.domain import DEFAULT_NUMERIC_TOLERANCE, StrategyKind, StrategySpec

if TYPE_CHECKING:
    np: Any
else:
    import numpy as np


class StrategyError(Exception):
    """Base class for typed strategy failures."""


class StrategyValidationError(StrategyError):
    """A strategy or its target weights violate the configured contract."""


class CSVWeightsError(StrategyValidationError):
    """An external daily-weight CSV violates its narrow typed contract."""


class Strategy(Protocol):
    """A strategy that returns close-stamped daily targets aligned to a dataset."""

    spec: StrategySpec

    def target_weights(self, dataset: StoredDataset) -> pd.DataFrame:
        """Return one target row per dataset date in canonical symbol order."""


def close_prices(dataset: StoredDataset) -> pd.DataFrame:
    """Extract canonical adjusted closes in the strategy's symbol layout."""
    try:
        closes = dataset.data.xs("close", axis="columns", level="field")
    except (KeyError, TypeError, ValueError) as error:
        raise StrategyValidationError("dataset does not contain canonical close prices") from error
    expected = pd.Index(
        [symbol.value for symbol in dataset.manifest.symbols],
        name="symbol",
    )
    if not closes.columns.equals(expected):
        raise StrategyValidationError("dataset close columns do not match its manifest symbols")
    return closes.astype(np.float64).copy()


def validate_target_weights(
    weights: pd.DataFrame,
    dataset: StoredDataset,
    spec: StrategySpec,
    numeric_tolerance: float,
) -> pd.DataFrame:
    """Validate aligned finite targets without clipping or normalizing them."""
    if not np.isfinite(numeric_tolerance) or not 0.0 < numeric_tolerance < 1.0:
        raise StrategyValidationError("numeric_tolerance must be finite and between zero and one")
    if not isinstance(weights.index, pd.DatetimeIndex) or not weights.index.equals(
        dataset.data.index
    ):
        raise StrategyValidationError("target-weight dates must exactly match dataset dates")

    expected_columns = pd.Index([symbol.value for symbol in spec.symbols], name="symbol")
    normalized_columns = pd.Index([str(column).strip().upper() for column in weights.columns])
    if normalized_columns.has_duplicates or not normalized_columns.equals(expected_columns):
        raise StrategyValidationError("target weights must contain only SPY and TLT in that order")
    if weights.isna().to_numpy().any():
        raise StrategyValidationError("target weights contain missing values")
    if any(
        not pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype)
        for dtype in weights.dtypes
    ):
        raise StrategyValidationError("target weights must be numeric")

    canonical = weights.astype(np.float64).copy()
    canonical.columns = expected_columns
    values = canonical.to_numpy(copy=False)
    if not np.isfinite(values).all():
        raise StrategyValidationError("target weights must be finite")
    if not spec.allow_short_exposure and (values < -numeric_tolerance).any():
        raise StrategyValidationError("short exposure is forbidden by StrategySpec")

    gross_exposure = canonical.abs().sum(axis="columns")
    if not np.isfinite(gross_exposure.to_numpy()).all():
        raise StrategyValidationError("target gross exposure must be finite")
    if not spec.allow_leverage and gross_exposure.gt(1.0 + numeric_tolerance).any():
        raise StrategyValidationError("leverage is forbidden by StrategySpec")

    net_exposure = canonical.sum(axis="columns")
    if not spec.allow_missing_weights:
        lower_bound = net_exposure.lt(1.0 - numeric_tolerance)
        upper_bound = net_exposure.gt(1.0 + numeric_tolerance)
        invalid_sum = lower_bound if spec.allow_leverage else lower_bound | upper_bound
        if invalid_sum.any():
            raise StrategyValidationError("target weights must sum to one within tolerance")
    elif not spec.allow_leverage and net_exposure.gt(1.0 + numeric_tolerance).any():
        raise StrategyValidationError("cash-permitted target weights cannot exceed full investment")
    return canonical


class FixedMonthly6040Strategy:
    """Fixed SPY/TLT holdings reset at initial and first monthly observed closes."""

    def __init__(
        self,
        spec: StrategySpec,
        numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    ) -> None:
        if spec.kind is not StrategyKind.MONTHLY_60_40:
            raise StrategyValidationError("monthly strategy requires kind=monthly_60_40")
        self.spec = spec
        self.numeric_tolerance = numeric_tolerance

    def rebalance_mask(self, dataset: StoredDataset) -> pd.Series:
        """Mark the initial close and each first observed close of a new month."""
        index = dataset.data.index
        periods = pd.Series(index.tz_localize(None).to_period("M"), index=index)
        mask = periods.ne(periods.shift(1))
        mask.iloc[0] = True
        return mask

    def rebalance_dates(self, dataset: StoredDataset) -> pd.DatetimeIndex:
        """Return deterministic decision dates for diagnostics and tests."""
        mask = self.rebalance_mask(dataset)
        return dataset.data.index[mask.to_numpy()]

    def target_weights_from_prices(
        self,
        dataset: StoredDataset,
        prices: pd.DataFrame,
    ) -> pd.DataFrame:
        """Return drifted holdings for an aligned positive in-memory price path."""
        expected_columns = pd.Index(
            [symbol.value for symbol in self.spec.symbols],
            name="symbol",
        )
        if not prices.index.equals(dataset.data.index) or not prices.columns.equals(
            expected_columns
        ):
            raise StrategyValidationError(
                "price-path dates and columns must exactly match the dataset"
            )
        price_values = prices.to_numpy(dtype=np.float64, copy=False)
        if not np.isfinite(price_values).all() or (price_values <= 0.0).any():
            raise StrategyValidationError("price-path values must be finite and positive")

        mask = self.rebalance_mask(dataset)
        anchor_prices = prices.copy()
        anchor_prices.loc[~mask, :] = np.nan
        anchor_prices = anchor_prices.ffill()

        configured = self.spec.target_weights
        if configured is None:  # guarded by StrategySpec, retained at the trust boundary
            raise StrategyValidationError("monthly strategy has no configured target weights")
        fixed = pd.Series(
            {symbol.value: configured[symbol] for symbol in self.spec.symbols},
            dtype=np.float64,
        )
        position_values = prices.div(anchor_prices).mul(fixed, axis="columns")
        targets = position_values.div(position_values.sum(axis="columns"), axis="index")
        targets.columns.name = "symbol"
        return validate_target_weights(
            targets,
            dataset,
            self.spec,
            self.numeric_tolerance,
        )

    def target_weights(self, dataset: StoredDataset) -> pd.DataFrame:
        """Return drifted holdings with prefix-invariant first-monthly-close resets."""
        return self.target_weights_from_prices(dataset, close_prices(dataset))


class CSVWeightsStrategy:
    """Adapter for close-stamped daily weights exported by another framework."""

    def __init__(
        self,
        spec: StrategySpec,
        csv_path: Path,
        numeric_tolerance: float = DEFAULT_NUMERIC_TOLERANCE,
    ) -> None:
        if spec.kind is not StrategyKind.EXTERNAL_WEIGHTS:
            raise StrategyValidationError("CSV adapter requires kind=external_weights")
        self.spec = spec
        self.csv_path = csv_path
        self.numeric_tolerance = numeric_tolerance

    def target_weights(self, dataset: StoredDataset) -> pd.DataFrame:
        """Load and validate exact daily rows; never forward-fill or normalize."""
        try:
            raw = pd.read_csv(self.csv_path)
        except (OSError, UnicodeError, pd.errors.ParserError) as error:
            raise CSVWeightsError(f"cannot read weights CSV: {self.csv_path}") from error
        if "date" not in raw.columns:
            raise CSVWeightsError("weights CSV must contain a date column")

        expected = [symbol.value for symbol in self.spec.symbols]
        supplied = set(raw.columns) - {"date"}
        unknown = sorted(supplied - set(expected))
        if unknown:
            raise CSVWeightsError(f"weights CSV contains unknown symbols: {', '.join(unknown)}")
        missing_columns = sorted(set(expected) - supplied)
        if missing_columns and not self.spec.allow_missing_weights:
            raise CSVWeightsError(
                f"weights CSV omits required symbols: {', '.join(missing_columns)}"
            )

        if raw["date"].isna().any():
            raise CSVWeightsError("weights CSV contains a missing date")
        try:
            parsed_dates = pd.DatetimeIndex(
                pd.to_datetime(raw["date"], format="%Y-%m-%d", errors="raise", utc=True),
                name="date",
            )
        except (TypeError, ValueError) as error:
            raise CSVWeightsError("weights CSV dates must use YYYY-MM-DD") from error
        if parsed_dates.has_duplicates:
            raise CSVWeightsError("weights CSV contains duplicate dates")

        extra_dates = parsed_dates.difference(dataset.data.index)
        missing_dates = dataset.data.index.difference(parsed_dates)
        if not extra_dates.empty:
            raise CSVWeightsError("weights CSV contains dates outside the dataset")
        if not missing_dates.empty and not self.spec.allow_missing_weights:
            raise CSVWeightsError("weights CSV dates must exactly match dataset dates")

        present_columns = [column for column in expected if column in raw.columns]
        try:
            numeric = raw.loc[:, present_columns].apply(pd.to_numeric, errors="raise")
        except (TypeError, ValueError) as error:
            raise CSVWeightsError("weights CSV contains a non-numeric weight") from error
        numeric.index = parsed_dates
        aligned = numeric.reindex(index=dataset.data.index, columns=expected)
        if aligned.isna().to_numpy().any():
            if not self.spec.allow_missing_weights:
                raise CSVWeightsError("weights CSV contains missing weights")
            # Explicit permission means an absent value is zero-weight cash,
            # never a filled decision.
            aligned = aligned.fillna(0.0)
        aligned.columns.name = "symbol"
        try:
            return validate_target_weights(
                aligned,
                dataset,
                self.spec,
                self.numeric_tolerance,
            )
        except StrategyValidationError as error:
            raise CSVWeightsError(str(error)) from error


def strategy_from_spec(
    spec: StrategySpec,
    numeric_tolerance: float,
    weights_csv: Path | None = None,
) -> Strategy:
    """Build only the strategy selected by a validated experiment contract."""
    if spec.kind is StrategyKind.MONTHLY_60_40:
        if weights_csv is not None:
            raise StrategyValidationError("monthly_60_40 does not accept --weights-csv")
        return FixedMonthly6040Strategy(spec, numeric_tolerance)
    if weights_csv is None:
        raise StrategyValidationError("external_weights requires --weights-csv")
    return CSVWeightsStrategy(spec, weights_csv, numeric_tolerance)
