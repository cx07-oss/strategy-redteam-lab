"""Build the deterministic Gate 8 correlation-break dataset fixture."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from strategy_redteam.data import DATA_FIELDS, LocalDatasetStore
from strategy_redteam.domain import AdjustmentPolicy, Symbol

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "offline-cache"
FIXTURE_MANIFEST = FIXTURE_ROOT / "manifests" / "correlation-break.json"


def build_fixture(root: Path = FIXTURE_ROOT) -> Path:
    """Create low-volatility negative correlation followed by a high-volatility break."""
    dates = pd.date_range("2024-01-02", periods=81, freq="B", tz="UTC", name="date")
    quiet_spy = np.resize(
        np.asarray([0.0030, -0.0020, 0.0040, -0.0030, 0.0010]),
        40,
    )
    quiet_tlt = np.resize(
        np.asarray([-0.0015, 0.0025, -0.0020, 0.0030, -0.0005]),
        40,
    )
    break_spy = np.resize(
        np.asarray([0.0120, -0.0180, 0.0090, -0.0150, -0.0040]),
        40,
    )
    break_tlt = np.resize(
        np.asarray([0.0090, -0.0140, 0.0070, -0.0120, -0.0030]),
        40,
    )
    returns = {
        Symbol.SPY: np.concatenate((quiet_spy, break_spy)),
        Symbol.TLT: np.concatenate((quiet_tlt, break_tlt)),
    }
    closes = {
        symbol: np.concatenate(([100.0], 100.0 * np.cumprod(1.0 + values)))
        for symbol, values in returns.items()
    }
    columns = pd.MultiIndex.from_product(
        ([Symbol.SPY.value, Symbol.TLT.value], DATA_FIELDS),
        names=("symbol", "field"),
    )
    values: dict[tuple[str, str], np.ndarray] = {}
    for symbol in (Symbol.SPY, Symbol.TLT):
        for field_name in DATA_FIELDS:
            values[(symbol.value, field_name)] = (
                np.full(len(dates), 1_000_000.0, dtype=np.float64)
                if field_name == "volume"
                else closes[symbol]
            )
    frame = pd.DataFrame(values, index=dates).loc[:, columns].astype(np.float64)
    stored = LocalDatasetStore(root).put(
        frame=frame,
        provider="deterministic-gate-8-fixture",
        source_identifiers={
            Symbol.SPY: "fixture:SPY",
            Symbol.TLT: "fixture:TLT",
        },
        symbols=(Symbol.SPY, Symbol.TLT),
        requested_start_date=dates[0].date(),
        requested_end_date=dates[-1].date(),
        adjustment_policy=AdjustmentPolicy.SPLITS_AND_DISTRIBUTIONS,
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    target = root / "manifests" / "correlation-break.json"
    if stored.manifest_path != target:
        if target.exists() and target.read_bytes() != stored.manifest_path.read_bytes():
            raise RuntimeError("fixture manifest alias already contains different bytes")
        if not target.exists():
            stored.manifest_path.replace(target)
    return target


if __name__ == "__main__":
    print(build_fixture())
