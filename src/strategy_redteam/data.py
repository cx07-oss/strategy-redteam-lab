"""Verified historical-data ingestion and immutable local storage.

Provider frames are untrusted inputs. They are validated and converted to one
canonical daily representation before their exact Parquet bytes are hashed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import pandas as pd  # type: ignore[import-untyped]
import yfinance as yf  # type: ignore[import-untyped]
from pydantic import ValidationError

from strategy_redteam.domain import AdjustmentPolicy, DataManifest, Symbol

PRICE_FIELDS = ("open", "high", "low", "close")
DATA_FIELDS = (*PRICE_FIELDS, "volume")
CALENDAR_POLICY = "common observed market dates; UTC midnight; reject non-monotonic dates"
if TYPE_CHECKING:
    np: Any
else:
    import numpy as np

MISSING_DATA_POLICY: Literal["reject"] = "reject"
PARQUET_MEDIA_TYPE: Literal["application/vnd.apache.parquet"] = (
    "application/vnd.apache.parquet"
)


class HistoricalDataError(Exception):
    """Base class for typed historical-data failures."""


class InvalidDataRequestError(HistoricalDataError):
    """The requested provider query is outside the fixed Gate 2 contract."""


class ProviderFailure(HistoricalDataError):
    """A provider could not return a usable response."""


class DataValidationError(HistoricalDataError):
    """Provider or stored data violates the canonical dataset contract."""


class DuplicateDateError(DataValidationError):
    """Two provider observations normalize to the same market date."""


class MissingDataError(DataValidationError):
    """At least one required value is missing under the reject policy."""


class NonFiniteDataError(DataValidationError):
    """At least one required value is NaN or infinite."""


class DatasetVerificationError(HistoricalDataError):
    """Immutable bytes, manifest bytes, or their declared metadata disagree."""


@dataclass(frozen=True)
class ProviderResult:
    """Raw provider result plus stable provider-specific identifiers."""

    data: pd.DataFrame
    source_identifiers: Mapping[Symbol, str]


class DataProvider(Protocol):
    """Injected source of adjusted daily OHLCV data."""

    name: str

    def fetch_daily(
        self,
        symbols: tuple[Symbol, ...],
        start_date: date,
        end_date: date,
        adjustment_policy: AdjustmentPolicy,
    ) -> ProviderResult:
        """Fetch an inclusive requested market-date period."""


class YFinanceDataProvider:
    """Explicit live provider using yfinance with adjusted daily prices."""

    name = "yfinance"

    def fetch_daily(
        self,
        symbols: tuple[Symbol, ...],
        start_date: date,
        end_date: date,
        adjustment_policy: AdjustmentPolicy,
    ) -> ProviderResult:
        """Download adjusted OHLC and volume without repairing provider data."""
        if adjustment_policy is not AdjustmentPolicy.SPLITS_AND_DISTRIBUTIONS:
            raise InvalidDataRequestError(f"unsupported adjustment policy: {adjustment_policy}")

        # yfinance's end boundary is exclusive; the public contract remains inclusive.
        provider_end = end_date + timedelta(days=1)
        symbol_values = tuple(symbol.value for symbol in symbols)
        try:
            raw = yf.download(
                tickers=list(symbol_values),
                start=start_date.isoformat(),
                end=provider_end.isoformat(),
                interval="1d",
                auto_adjust=True,
                actions=False,
                repair=False,
                keepna=True,
                progress=False,
                threads=False,
                group_by="ticker",
                multi_level_index=True,
            )
        except Exception as error:
            raise ProviderFailure(f"yfinance download failed: {error}") from error

        if raw is None or raw.empty:
            raise ProviderFailure("yfinance returned no rows for the requested period")
        if not isinstance(raw.columns, pd.MultiIndex) or raw.columns.nlevels != 2:
            raise ProviderFailure("yfinance returned an unexpected column layout")
        if raw.columns.has_duplicates:
            raise ProviderFailure("yfinance returned duplicate columns")

        provider_columns = {
            (symbol, field): (symbol, field.title())
            for symbol in symbol_values
            for field in DATA_FIELDS
        }
        missing = tuple(
            provider_column
            for provider_column in provider_columns.values()
            if provider_column not in raw.columns
        )
        if missing:
            raise ProviderFailure(f"yfinance response omitted required columns: {missing}")

        selected = raw.loc[:, list(provider_columns.values())].copy()
        selected.columns = pd.MultiIndex.from_tuples(
            provider_columns,
            names=("symbol", "field"),
        )
        identifiers = {symbol: f"yahoo:{symbol.value}" for symbol in symbols}
        return ProviderResult(data=selected, source_identifiers=identifiers)


@dataclass(frozen=True)
class StoredDataset:
    """A manifest-verified immutable dataset from a local or remote store."""

    manifest: DataManifest
    data: pd.DataFrame
    dataset_path: Path | None
    manifest_path: Path | None
    manifest_sha256: str


@dataclass(frozen=True)
class CacheResult:
    """Result of a verified cache lookup or one provider ingestion."""

    stored: StoredDataset
    cache_hit: bool


def canonical_symbols(symbols: Sequence[str | Symbol]) -> tuple[Symbol, ...]:
    """Validate the fixed MVP universe and return its canonical ordering."""
    try:
        parsed = tuple(Symbol(str(symbol).strip().upper()) for symbol in symbols)
    except ValueError as error:
        raise InvalidDataRequestError("only SPY and TLT are supported") from error
    if len(parsed) != len(set(parsed)):
        raise InvalidDataRequestError("requested symbols must be unique")
    expected = (Symbol.SPY, Symbol.TLT)
    if set(parsed) != set(expected):
        raise InvalidDataRequestError("the MVP dataset requires exactly SPY and TLT")
    return expected


def validate_requested_period(start_date: date, end_date: date) -> None:
    """Reject reversed periods rather than changing their meaning."""
    if start_date > end_date:
        raise InvalidDataRequestError("start_date must be on or before end_date")


def canonicalize_provider_data(
    frame: pd.DataFrame,
    symbols: tuple[Symbol, ...],
    requested_start_date: date,
    requested_end_date: date,
) -> pd.DataFrame:
    """Validate and canonicalize a provider frame with vectorized operations."""
    validate_requested_period(requested_start_date, requested_end_date)
    if frame.empty:
        raise DataValidationError("provider data contains no rows")
    if not isinstance(frame.columns, pd.MultiIndex) or frame.columns.nlevels != 2:
        raise DataValidationError("data columns must have symbol and field levels")
    if frame.columns.has_duplicates:
        raise DataValidationError("data columns must be unique")

    normalized_columns = pd.MultiIndex.from_tuples(
        [
            (str(symbol).strip().upper(), str(field).strip().lower())
            for symbol, field in frame.columns.to_list()
        ],
        names=("symbol", "field"),
    )
    if normalized_columns.has_duplicates:
        raise DataValidationError("data columns collide after label normalization")

    expected_columns = pd.MultiIndex.from_product(
        ([symbol.value for symbol in symbols], DATA_FIELDS),
        names=("symbol", "field"),
    )
    if set(normalized_columns.to_list()) != set(expected_columns.to_list()):
        raise DataValidationError("data must contain exactly adjusted OHLCV for every symbol")

    normalized = frame.copy()
    normalized.columns = normalized_columns
    normalized = normalized.loc[:, expected_columns]
    if any(
        not pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype)
        for dtype in normalized.dtypes
    ):
        raise DataValidationError("all OHLCV columns must have numeric dtypes")

    try:
        daily_index = pd.DatetimeIndex(pd.to_datetime(normalized.index, utc=True)).normalize()
    except (TypeError, ValueError) as error:
        raise DataValidationError("data index must contain valid market dates") from error
    daily_index.name = "date"
    if daily_index.hasnans:
        raise DataValidationError("data index contains a missing market date")
    if daily_index.has_duplicates:
        raise DuplicateDateError("data contains duplicate normalized market dates")
    if not daily_index.is_monotonic_increasing:
        raise DataValidationError("market dates must be strictly increasing")
    first_date = daily_index[0].date()
    last_date = daily_index[-1].date()
    if first_date < requested_start_date or last_date > requested_end_date:
        raise DataValidationError("provider returned data outside the requested period")

    if normalized.isna().to_numpy().any():
        raise MissingDataError("missing OHLCV values are rejected; prices are not filled")
    values = normalized.to_numpy(dtype=np.float64, copy=False)
    if not np.isfinite(values).all():
        raise NonFiniteDataError("OHLCV values must all be finite")

    price_values = normalized.loc[:, pd.IndexSlice[:, PRICE_FIELDS]].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    if (price_values <= 0.0).any():
        raise DataValidationError("adjusted OHLC prices must be strictly positive")
    volume_values = normalized.loc[:, pd.IndexSlice[:, "volume"]].to_numpy(
        dtype=np.float64,
        copy=False,
    )
    if (volume_values < 0.0).any():
        raise DataValidationError("volume must be non-negative")

    canonical = normalized.astype(np.float64).copy()
    canonical.index = daily_index
    canonical.columns = expected_columns
    return canonical


def canonical_column_contract(frame: pd.DataFrame) -> tuple[str, ...]:
    """Flatten canonical columns for the manifest's ordered column contract."""
    return ("date", *(f"{symbol}.{field}" for symbol, field in frame.columns.to_list()))


def canonical_parquet_bytes(frame: pd.DataFrame) -> bytes:
    """Serialize canonical data with fixed Parquet options."""
    buffer = BytesIO()
    frame.to_parquet(
        buffer,
        engine="pyarrow",
        index=True,
        compression="zstd",
        version="2.6",
        data_page_version="1.0",
        use_dictionary=False,
        write_statistics=True,
    )
    return buffer.getvalue()


def sha256_bytes(content: bytes) -> str:
    """Return a lowercase SHA-256 digest."""
    return hashlib.sha256(content).hexdigest()


def canonical_data_sha256(frame: pd.DataFrame) -> str:
    """Hash the exact deterministic Parquet representation."""
    return sha256_bytes(canonical_parquet_bytes(frame))


def canonical_manifest_bytes(manifest: DataManifest) -> bytes:
    """Serialize a manifest canonically for its external self-hash."""
    payload = manifest.model_dump(mode="json")
    return (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def validate_dataset_bytes(
    *,
    manifest_bytes: bytes,
    parquet_bytes: bytes,
    dataset_path: Path | None = None,
    manifest_path: Path | None = None,
) -> StoredDataset:
    """Validate immutable manifest and Parquet bytes without trusting their location."""
    try:
        manifest = DataManifest.model_validate_json(manifest_bytes)
    except ValidationError as error:
        raise DatasetVerificationError(
            f"manifest schema validation failed: {error}"
        ) from error
    if manifest_bytes != canonical_manifest_bytes(manifest):
        raise DatasetVerificationError("manifest is not canonical or its bytes were altered")
    if len(parquet_bytes) != manifest.byte_length:
        raise DatasetVerificationError("dataset byte length does not match manifest")
    if sha256_bytes(parquet_bytes) != manifest.sha256:
        raise DatasetVerificationError("dataset SHA-256 does not match manifest")

    try:
        loaded = pd.read_parquet(BytesIO(parquet_bytes), engine="pyarrow")
    except Exception as error:
        raise DatasetVerificationError("dataset is not readable Parquet") from error
    symbols = tuple(manifest.symbols)
    try:
        canonical = canonicalize_provider_data(
            loaded,
            symbols,
            manifest.requested_start_date,
            manifest.requested_end_date,
        )
    except DataValidationError as error:
        raise DatasetVerificationError(
            f"stored dataset is not canonical: {error}"
        ) from error
    if not loaded.index.equals(canonical.index) or not loaded.columns.equals(
        canonical.columns
    ):
        raise DatasetVerificationError("stored dataset index or columns are not canonical")
    if not loaded.equals(canonical):
        raise DatasetVerificationError("stored dataset values or dtypes are not canonical")
    if (
        manifest.start_date != canonical.index[0].date()
        or manifest.end_date != canonical.index[-1].date()
        or manifest.row_count != len(canonical.index)
        or manifest.columns != canonical_column_contract(canonical)
        or manifest.calendar_policy != CALENDAR_POLICY
        or manifest.missing_data_policy != MISSING_DATA_POLICY
    ):
        raise DatasetVerificationError("manifest metadata does not agree with dataset")

    return StoredDataset(
        manifest=manifest,
        data=canonical,
        dataset_path=dataset_path,
        manifest_path=manifest_path,
        manifest_sha256=sha256_bytes(manifest_bytes),
    )


def _query_sha256(
    provider: str,
    symbols: tuple[Symbol, ...],
    start_date: date,
    end_date: date,
    adjustment_policy: AdjustmentPolicy,
) -> str:
    payload = {
        "adjustment_policy": adjustment_policy.value,
        "end_date": end_date.isoformat(),
        "provider": provider,
        "schema_version": "1.0",
        "start_date": start_date.isoformat(),
        "symbols": [symbol.value for symbol in symbols],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return sha256_bytes(encoded)


class LocalDatasetStore:
    """Content-addressed Parquet objects with immutable canonical manifests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.datasets_dir = root / "datasets"
        self.manifests_dir = root / "manifests"

    def manifest_path_for_request(
        self,
        provider: str,
        symbols: tuple[Symbol, ...],
        start_date: date,
        end_date: date,
        adjustment_policy: AdjustmentPolicy,
    ) -> Path:
        """Return the deterministic request-keyed manifest path."""
        query_hash = _query_sha256(
            provider,
            symbols,
            start_date,
            end_date,
            adjustment_policy,
        )
        return self.manifests_dir / f"{query_hash}.json"

    def lookup(
        self,
        provider: str,
        symbols: tuple[Symbol, ...],
        start_date: date,
        end_date: date,
        adjustment_policy: AdjustmentPolicy,
    ) -> StoredDataset | None:
        """Return only a fully verified cache hit."""
        manifest_path = self.manifest_path_for_request(
            provider,
            symbols,
            start_date,
            end_date,
            adjustment_policy,
        )
        if not manifest_path.exists():
            return None
        stored = self.validate(manifest_path)
        manifest = stored.manifest
        if (
            manifest.provider != provider
            or manifest.symbols != symbols
            or manifest.requested_start_date != start_date
            or manifest.requested_end_date != end_date
            or manifest.adjustment_policy != adjustment_policy
        ):
            raise DatasetVerificationError("cache manifest does not match its request key")
        return stored

    def put(
        self,
        frame: pd.DataFrame,
        provider: str,
        source_identifiers: Mapping[Symbol, str],
        symbols: tuple[Symbol, ...],
        requested_start_date: date,
        requested_end_date: date,
        adjustment_policy: AdjustmentPolicy,
        retrieved_at: datetime,
    ) -> StoredDataset:
        """Write new content once; existing objects are never overwritten."""
        parquet_bytes = canonical_parquet_bytes(frame)
        data_hash = sha256_bytes(parquet_bytes)
        dataset_path = self.datasets_dir / f"{data_hash}.parquet"
        manifest_path = self.manifest_path_for_request(
            provider,
            symbols,
            requested_start_date,
            requested_end_date,
            adjustment_policy,
        )
        try:
            manifest = DataManifest(
                dataset_id=f"dataset-{data_hash}",
                provider=provider,
                source_identifiers=dict(source_identifiers),
                symbols=symbols,
                requested_start_date=requested_start_date,
                requested_end_date=requested_end_date,
                start_date=frame.index[0].date(),
                end_date=frame.index[-1].date(),
                adjustment_policy=adjustment_policy,
                calendar_policy=CALENDAR_POLICY,
                missing_data_policy=MISSING_DATA_POLICY,
                row_count=len(frame.index),
                columns=canonical_column_contract(frame),
                retrieved_at=retrieved_at,
                media_type=PARQUET_MEDIA_TYPE,
                byte_length=len(parquet_bytes),
                sha256=data_hash,
            )
        except ValidationError as error:
            raise DataValidationError(f"manifest construction failed: {error}") from error
        manifest_bytes = canonical_manifest_bytes(manifest)

        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        _write_once(dataset_path, parquet_bytes)
        _write_once(manifest_path, manifest_bytes)
        return self.validate(manifest_path)

    def validate(self, manifest_path: Path) -> StoredDataset:
        """Verify canonical manifest bytes, exact data bytes, and data agreement."""
        try:
            manifest_bytes = manifest_path.read_bytes()
        except OSError as error:
            raise DatasetVerificationError(f"cannot read manifest: {manifest_path}") from error
        try:
            manifest = DataManifest.model_validate_json(manifest_bytes)
        except ValidationError as error:
            raise DatasetVerificationError(f"manifest schema validation failed: {error}") from error
        dataset_path = self.datasets_dir / f"{manifest.sha256}.parquet"
        try:
            parquet_bytes = dataset_path.read_bytes()
        except OSError as error:
            raise DatasetVerificationError(
                f"cannot read declared dataset: {dataset_path}"
            ) from error
        return validate_dataset_bytes(
            manifest_bytes=manifest_bytes,
            parquet_bytes=parquet_bytes,
            dataset_path=dataset_path,
            manifest_path=manifest_path,
        )


class HistoricalDataService:
    """Dependency-injected verified-cache workflow."""

    def __init__(
        self,
        store: LocalDatasetStore,
        provider: DataProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.clock = clock or (lambda: datetime.now(UTC))

    def get_or_download(
        self,
        symbols: Sequence[str | Symbol],
        start_date: date,
        end_date: date,
        adjustment_policy: AdjustmentPolicy = AdjustmentPolicy.SPLITS_AND_DISTRIBUTIONS,
    ) -> CacheResult:
        """Use a verified hit or fetch, validate, and cache one immutable dataset."""
        ordered_symbols = canonical_symbols(symbols)
        validate_requested_period(start_date, end_date)
        cached = self.store.lookup(
            self.provider.name,
            ordered_symbols,
            start_date,
            end_date,
            adjustment_policy,
        )
        if cached is not None:
            return CacheResult(stored=cached, cache_hit=True)

        try:
            result = self.provider.fetch_daily(
                ordered_symbols,
                start_date,
                end_date,
                adjustment_policy,
            )
        except HistoricalDataError:
            raise
        except Exception as error:
            raise ProviderFailure(f"{self.provider.name} provider failed: {error}") from error
        canonical = canonicalize_provider_data(
            result.data,
            ordered_symbols,
            start_date,
            end_date,
        )
        retrieved_at = self.clock()
        stored = self.store.put(
            canonical,
            self.provider.name,
            result.source_identifiers,
            ordered_symbols,
            start_date,
            end_date,
            adjustment_policy,
            retrieved_at,
        )
        return CacheResult(stored=stored, cache_hit=False)


def _write_once(path: Path, content: bytes) -> None:
    """Create immutable content, accepting only an identical existing object."""
    try:
        with path.open("xb") as output:
            output.write(content)
    except FileExistsError as collision:
        try:
            existing = path.read_bytes()
        except OSError as error:
            raise DatasetVerificationError(
                f"cannot verify existing immutable file: {path}"
            ) from error
        if existing != content:
            raise DatasetVerificationError(
                f"refusing to overwrite immutable file: {path}"
            ) from collision
