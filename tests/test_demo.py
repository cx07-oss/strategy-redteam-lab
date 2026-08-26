"""Network-free tests for exporting genuine verified demo telemetry."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_redteam.demo import DemoExportError, export_verified_demo_telemetry, run_ollama_demo
from strategy_redteam.offline import run_offline_experiment
from strategy_redteam.telemetry import RunTelemetry

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "example_60_40.yaml"
MANIFEST = ROOT / "tests" / "fixtures" / "offline-cache" / "manifests" / "correlation-break.json"


def test_exported_demo_is_exact_validated_telemetry_from_a_verified_run(tmp_path: Path) -> None:
    """The exporter copies no invented values and rejects output-file replacement."""
    run_directory = tmp_path / "verified-run"
    run_offline_experiment(
        config_path=CONFIG,
        manifest_path=MANIFEST,
        artifact_directory=run_directory,
    )
    destination = tmp_path / "demo" / "demo-telemetry.json"

    telemetry = export_verified_demo_telemetry(
        run_directory=run_directory,
        destination=destination,
    )

    assert RunTelemetry.model_validate_json(destination.read_bytes()) == telemetry
    assert destination.read_bytes() == (run_directory / "telemetry.json").read_bytes()
    assert telemetry.evaluations
    assert telemetry.events
    with pytest.raises(DemoExportError, match="immutable"):
        export_verified_demo_telemetry(run_directory=run_directory, destination=destination)


def test_ollama_demo_rejects_non_ollama_configuration(tmp_path: Path) -> None:
    """The production demo command cannot silently use deterministic or cloud providers."""
    with pytest.raises(DemoExportError, match=r"require model_provider\.provider: ollama"):
        run_ollama_demo(
            config_path=CONFIG,
            manifest_path=MANIFEST,
            output_directory=tmp_path / "demo",
        )
