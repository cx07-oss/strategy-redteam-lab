"""Export genuine, verified local-agent runs as static-demo telemetry artifacts."""

from __future__ import annotations

from pathlib import Path

from strategy_redteam.model_provider import ModelProviderName
from strategy_redteam.offline import (
    OfflineArtifactError,
    OfflineRunArtifact,
    load_offline_config,
    run_offline_experiment,
    verify_offline_artifacts,
)
from strategy_redteam.telemetry import RunTelemetry

DEMO_TELEMETRY_FILENAME = "demo-telemetry.json"


class DemoExportError(RuntimeError):
    """A local Ollama demonstration could not produce verified telemetry."""


def export_verified_demo_telemetry(*, run_directory: Path, destination: Path) -> RunTelemetry:
    """Validate an existing complete run and copy only canonical telemetry to a new file."""
    try:
        verify_offline_artifacts(run_directory)
        source = (run_directory / "telemetry.json").read_bytes()
        telemetry = RunTelemetry.model_validate_json(source)
    except (OSError, ValueError, OfflineArtifactError) as error:
        raise DemoExportError("demo source run is not a verified telemetry bundle") from error
    if source != telemetry.canonical_json_bytes():
        raise DemoExportError("source telemetry is not canonical")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(source)
    except OSError as error:
        raise DemoExportError("could not create immutable demo telemetry artifact") from error
    return telemetry


def run_ollama_demo(
    *,
    config_path: Path,
    manifest_path: Path,
    output_directory: Path,
) -> tuple[OfflineRunArtifact, RunTelemetry]:
    """Run the actual Ollama attacker/defender workflow and export validated telemetry."""
    config = load_offline_config(config_path)
    if config.model_provider.provider is not ModelProviderName.OLLAMA:
        raise DemoExportError("demo runs require model_provider.provider: ollama")
    output_directory = output_directory.resolve()
    if output_directory.exists():
        raise DemoExportError("demo output directory already exists")
    run_directory = output_directory / "run"
    try:
        result = run_offline_experiment(
            config_path=config_path,
            manifest_path=manifest_path,
            artifact_directory=run_directory,
        )
        telemetry = export_verified_demo_telemetry(
            run_directory=run_directory,
            destination=output_directory / DEMO_TELEMETRY_FILENAME,
        )
    except (OSError, OfflineArtifactError) as error:
        raise DemoExportError("local Ollama demo workflow failed") from error
    return result, telemetry
