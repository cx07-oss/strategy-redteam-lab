"""Create a chart-efficient, exact-point projection of canonical MVP 3 evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _sample(values: list[dict[str, Any]], step: int) -> list[dict[str, Any]]:
    selected = values[::step]
    if values and selected[-1] != values[-1]:
        selected.append(values[-1])
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    research = payload["research"]
    original_equity_count = len(research["equity_curve"])
    original_assignment_count = len(research["regime_assignments"])
    research["equity_curve"] = _sample(research["equity_curve"], 10)
    research["regime_assignments"] = _sample(research["regime_assignments"], 5)
    payload["presentation_sampling"] = {
        "method": "stable_stride_with_final_point",
        "equity_stride": 10,
        "equity_source_count": original_equity_count,
        "regime_stride": 5,
        "regime_source_count": original_assignment_count,
        "metrics_unchanged": True,
    }
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if args.destination.suffix == ".mjs":
        args.destination.write_text(
            "/** @type {unknown} */\nexport default JSON.parse(String.raw`"
            + serialized
            + "`);\n",
            encoding="utf-8",
        )
    else:
        args.destination.write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
