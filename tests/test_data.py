"""Deterministic Gate 2 historical-data tests with no network access."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from strategy_redteam.cli import app
from strategy_redteam.data import (
    DATA_FIELDS,
    CacheResult,
    DatasetVerificationError,
    DataValidationError,
    DuplicateDateError,
    HistoricalDataService,
    LocalDatasetStore,
    MissingDataError,
    NonFiniteDataError,
    ProviderFailure,
    ProviderResult,
    canonical_data_sha256,
    canonicalize_provider_data,
)
from strategy_redteam.domain import AdjustmentPolicy, Symbol

START = date(2020, 1, 2)
END = date(2020, 1, 6)
RETRIEVED_AT = datetime(2026, 8, 23, 1, 2, 3, tzinfo=UTC)


def make_provider_frame(
    *,
    index: pd.Index | None = None,
    symbol_order: tuple[str, ...] = ("TLT", "SPY"),
) -> pd.DataFrame:
    """Return three tiny adjusted OHLCV observations in provider order."""
    dates = index if index is not None else pd.Index(["2020-01-02", "2020-01-03", "2020-01-06"])
    columns = pd.MultiIndex.from_product(
        (symbol_order, tuple(reversed(DATA_FIELDS))),
        names=("ticker", "price"),
    )
    values = np.arange(1, len(dates) * len(columns) + 1, dtype=np.float64).reshape(
        len(dates), len(columns)
    )
    return pd.DataFrame(values, index=dates, columns=columns)


@dataclass
class FakeDataProvider:
    """Injected deterministic provider that records calls and never uses a network."""

    data: pd.DataFrame
    failure: Exception | None = None
    name: str = "fake-provider"
    calls: int = 0
    requests: list[tuple[tuple[Symbol, ...], date, date, AdjustmentPolicy]] = field(
        default_factory=list
    )

    def fetch_daily(
        self,
        symbols: tuple[Symbol, ...],
        start_date: date,
        end_date: date,
        adjustment_policy: AdjustmentPolicy,
    ) -> ProviderResult:
        self.calls += 1
        self.requests.append((symbols, start_date, end_date, adjustment_policy))
        if self.failure is not None:
            raise self.failure
        return ProviderResult(
            data=self.data.copy(),
            source_identifiers={symbol: f"fixture:{symbol.value}" for symbol in symbols},
        )


def make_service(tmp_path: Path, provider: FakeDataProvider) -> HistoricalDataService:
    """Build the data workflow with an injected fake and deterministic clock."""
    return HistoricalDataService(
        LocalDatasetStore(tmp_path / "cache"),
        provider,
        clock=lambda: RETRIEVED_AT,
    )


def download_fixture(tmp_path: Path) -> tuple[FakeDataProvider, CacheResult]:
    provider = FakeDataProvider(make_provider_frame())
    return provider, make_service(tmp_path, provider).get_or_download(("TLT", "SPY"), START, END)


def test_normalized_output_and_multi_symbol_ordering() -> None:
    """Provider labels and timestamps normalize to the one canonical layout."""
    source = make_provider_frame(
        index=pd.DatetimeIndex(
            ["2020-01-02 16:00-05:00", "2020-01-03 16:00-05:00", "2020-01-06 16:00-05:00"]
        )
    )
    result = canonicalize_provider_data(source, (Symbol.SPY, Symbol.TLT), START, END)

    expected_columns = pd.MultiIndex.from_product(
        (("SPY", "TLT"), DATA_FIELDS),
        names=("symbol", "field"),
    )
    expected_index = pd.DatetimeIndex(
        ["2020-01-02", "2020-01-03", "2020-01-06"],
        tz="UTC",
        name="date",
    )
    assert result.columns.equals(expected_columns)
    assert result.index.equals(expected_index)
    assert (result.dtypes == np.dtype("float64")).all()


def test_duplicate_normalized_dates_are_rejected() -> None:
    """Two timestamps on one market date are not silently deduplicated."""
    frame = make_provider_frame(
        index=pd.Index(["2020-01-02 09:00", "2020-01-02 16:00", "2020-01-03 16:00"])
    )
    with pytest.raises(DuplicateDateError, match="duplicate"):
        canonicalize_provider_data(frame, (Symbol.SPY, Symbol.TLT), START, END)


def test_missing_values_are_rejected_without_filling() -> None:
    frame = make_provider_frame()
    frame.iloc[1, 2] = np.nan
    with pytest.raises(MissingDataError, match="not filled"):
        canonicalize_provider_data(frame, (Symbol.SPY, Symbol.TLT), START, END)


@pytest.mark.parametrize("value", [np.inf, -np.inf])
def test_nonfinite_values_are_rejected(value: float) -> None:
    frame = make_provider_frame()
    frame.iloc[1, 2] = value
    with pytest.raises(NonFiniteDataError, match="finite"):
        canonicalize_provider_data(frame, (Symbol.SPY, Symbol.TLT), START, END)


def test_non_positive_prices_are_rejected() -> None:
    frame = make_provider_frame()
    frame.loc["2020-01-03", ("SPY", "close")] = 0.0
    with pytest.raises(DataValidationError, match="strictly positive"):
        canonicalize_provider_data(frame, (Symbol.SPY, Symbol.TLT), START, END)


def test_non_monotonic_dates_are_rejected_not_sorted() -> None:
    frame = make_provider_frame(index=pd.Index(["2020-01-03", "2020-01-02", "2020-01-06"]))
    with pytest.raises(DataValidationError, match="strictly increasing"):
        canonicalize_provider_data(frame, (Symbol.SPY, Symbol.TLT), START, END)


def test_provider_cannot_silently_expand_requested_period() -> None:
    frame = make_provider_frame(index=pd.Index(["2020-01-01", "2020-01-02", "2020-01-03"]))
    with pytest.raises(DataValidationError, match="outside the requested period"):
        canonicalize_provider_data(frame, (Symbol.SPY, Symbol.TLT), START, END)


def test_canonical_hash_is_stable_across_provider_column_order() -> None:
    first = canonicalize_provider_data(
        make_provider_frame(symbol_order=("SPY", "TLT")),
        (Symbol.SPY, Symbol.TLT),
        START,
        END,
    )
    second = canonicalize_provider_data(
        make_provider_frame(symbol_order=("SPY", "TLT")).loc[:, ::-1],
        (Symbol.SPY, Symbol.TLT),
        START,
        END,
    )
    assert first.equals(second)
    assert canonical_data_sha256(first) == canonical_data_sha256(first.copy())
    assert canonical_data_sha256(first) == canonical_data_sha256(second)


def test_manifest_records_requested_actual_and_provenance_fields(tmp_path: Path) -> None:
    provider, result = download_fixture(tmp_path)
    manifest = result.stored.manifest

    assert not result.cache_hit
    assert provider.requests == [
        (
            (Symbol.SPY, Symbol.TLT),
            START,
            END,
            AdjustmentPolicy.SPLITS_AND_DISTRIBUTIONS,
        )
    ]
    assert manifest.provider == "fake-provider"
    assert manifest.symbols == (Symbol.SPY, Symbol.TLT)
    assert manifest.requested_start_date == START
    assert manifest.requested_end_date == END
    assert manifest.start_date == date(2020, 1, 2)
    assert manifest.end_date == date(2020, 1, 6)
    assert manifest.row_count == 3
    assert manifest.retrieved_at == RETRIEVED_AT
    assert manifest.missing_data_policy == "reject"
    assert manifest.media_type == "application/vnd.apache.parquet"
    assert result.stored.dataset_path.name == f"{manifest.sha256}.parquet"


def test_verified_cache_reuse_does_not_call_provider_again(tmp_path: Path) -> None:
    provider = FakeDataProvider(make_provider_frame())
    service = make_service(tmp_path, provider)

    first = service.get_or_download(("TLT", "SPY"), START, END)
    second = service.get_or_download(("SPY", "TLT"), START, END)

    assert not first.cache_hit
    assert second.cache_hit
    assert provider.calls == 1
    assert second.stored.manifest == first.stored.manifest
    assert second.stored.data.equals(first.stored.data)


@pytest.mark.parametrize("target", ["dataset", "manifest"])
def test_tampering_is_detected_before_cache_reuse(tmp_path: Path, target: str) -> None:
    provider = FakeDataProvider(make_provider_frame())
    service = make_service(tmp_path, provider)
    first = service.get_or_download(("SPY", "TLT"), START, END)
    path = (
        first.stored.dataset_path if target == "dataset" else first.stored.manifest_path
    )
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(DatasetVerificationError):
        service.get_or_download(("SPY", "TLT"), START, END)
    assert provider.calls == 1


def test_provider_failure_is_typed_and_writes_no_cache(tmp_path: Path) -> None:
    provider = FakeDataProvider(make_provider_frame(), failure=RuntimeError("fixture outage"))
    service = make_service(tmp_path, provider)

    with pytest.raises(ProviderFailure, match="fixture outage"):
        service.get_or_download(("SPY", "TLT"), START, END)
    assert provider.calls == 1
    assert not (tmp_path / "cache").exists()


def test_validate_cli_is_repeatable_and_network_free(tmp_path: Path) -> None:
    _, result = download_fixture(tmp_path)
    runner = CliRunner()

    first = runner.invoke(app, ["data", "validate", str(result.stored.manifest_path)])
    second = runner.invoke(app, ["data", "validate", str(result.stored.manifest_path)])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "validation=passed" in first.output
    assert first.output == second.output
