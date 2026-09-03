"""Local commands for immutable data and deterministic baseline replay."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from strategy_redteam.backtest import BacktestError, run_backtest
from strategy_redteam.data import (
    HistoricalDataError,
    HistoricalDataService,
    LocalDatasetStore,
    YFinanceDataProvider,
)
from strategy_redteam.demo import DemoExportError, run_ollama_demo
from strategy_redteam.domain import ExperimentSpec
from strategy_redteam.offline import (
    OfflineRunError,
    load_offline_config,
    offline_artifact_names,
    run_offline_experiment,
)
from strategy_redteam.product import build_canonical_product_artifact
from strategy_redteam.research import (
    ExecutionCostAssumptions,
    Experiment,
    ExperimentResult,
    RegimeConfig,
    WalkForwardConfig,
    run_research_experiment,
)
from strategy_redteam.strategy import StrategyError, strategy_from_spec

app = typer.Typer(help="Research-only trading-strategy red-team tools.")
data_app = typer.Typer(help="Download, cache, and validate immutable historical datasets.")
demo_app = typer.Typer(help="Run and export genuine verified local-Ollama demo telemetry.")
research_app = typer.Typer(help="Run deterministic quantitative and ML research experiments.")
product_app = typer.Typer(help="Build precomputed, deterministic public product evidence.")
app.add_typer(data_app, name="data")
app.add_typer(demo_app, name="demo")
app.add_typer(research_app, name="research")
app.add_typer(product_app, name="product")


class RunMode(StrEnum):
    """Execution modes intentionally available in the local MVP."""

    OFFLINE = "offline"


def _parse_iso_date(value: str, option_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter("must use YYYY-MM-DD", param_hint=option_name) from error


@data_app.command("download")
def download_data(
    start: Annotated[str, typer.Option(help="Inclusive requested start date (YYYY-MM-DD).")],
    end: Annotated[str, typer.Option(help="Inclusive requested end date (YYYY-MM-DD).")],
    cache_dir: Annotated[
        Path,
        typer.Option(file_okay=False, resolve_path=True, help="Local immutable dataset cache."),
    ] = Path(".data-cache"),
    symbol: Annotated[
        list[str] | None,
        typer.Option("--symbol", help="Symbol; repeat for SPY and TLT."),
    ] = None,
) -> None:
    """Download adjusted daily OHLCV or reuse a manifest-verified cache hit."""
    requested_symbols = symbol if symbol is not None else ["SPY", "TLT"]
    service = HistoricalDataService(LocalDatasetStore(cache_dir), YFinanceDataProvider())
    try:
        result = service.get_or_download(
            requested_symbols,
            _parse_iso_date(start, "--start"),
            _parse_iso_date(end, "--end"),
        )
    except HistoricalDataError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error

    status = "verified_hit" if result.cache_hit else "downloaded"
    stored = result.stored
    typer.echo(f"cache_status={status}")
    typer.echo(f"dataset_id={stored.manifest.dataset_id}")
    typer.echo(f"data_sha256={stored.manifest.sha256}")
    typer.echo(f"manifest_sha256={stored.manifest_sha256}")
    typer.echo(f"manifest={stored.manifest_path}")


@data_app.command("validate")
def validate_data(
    manifest: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
) -> None:
    """Validate an existing canonical manifest and its content-addressed dataset."""
    store = LocalDatasetStore(manifest.parent.parent)
    try:
        stored = store.validate(manifest)
    except HistoricalDataError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo("validation=passed")
    typer.echo(f"dataset_id={stored.manifest.dataset_id}")
    typer.echo(f"data_sha256={stored.manifest.sha256}")
    typer.echo(f"manifest_sha256={stored.manifest_sha256}")


@app.command("baseline")
def run_baseline(
    experiment: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    manifest: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    weights_csv: Annotated[
        Path | None,
        typer.Option(
            "--weights-csv",
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Required only for an external_weights StrategySpec.",
        ),
    ] = None,
) -> None:
    """Run a configured baseline against an existing verified immutable dataset."""
    try:
        spec = ExperimentSpec.model_validate_json(experiment.read_bytes())
        stored = LocalDatasetStore(manifest.parent.parent).validate(manifest)
        if spec.dataset_id != stored.manifest.dataset_id:
            raise BacktestError("ExperimentSpec dataset_id does not match the manifest")
        if spec.data_sha256 != stored.manifest.sha256:
            raise BacktestError("ExperimentSpec data_sha256 does not match the manifest")
        strategy = strategy_from_spec(spec.strategy, spec.numeric_tolerance, weights_csv)
        result = run_backtest(
            stored,
            strategy,
            spec.transaction_cost_bps,
            spec.numeric_tolerance,
            spec.failure_rules,
        )
    except (OSError, ValidationError, HistoricalDataError, StrategyError, BacktestError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error

    payload = {
        "breaches": [
            breach.model_dump(mode="json") for breach in result.failure_evaluation.breaches
        ],
        "data_sha256": stored.manifest.sha256,
        "dataset_id": stored.manifest.dataset_id,
        "experiment_id": spec.experiment_id,
        "final_equity": float(result.equity_curve.iloc[-1]),
        "metrics": result.metrics.model_dump(mode="json"),
        "non_evaluable_windows": [
            asdict(disclosure) for disclosure in result.failure_evaluation.non_evaluable_windows
        ],
        "schema_version": "1.0",
        "strategy_id": spec.strategy.strategy_id,
        "transaction_cost_bps": spec.transaction_cost_bps,
    }
    typer.echo(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


@app.command("run")
def run_vertical_slice(
    experiment: Annotated[
        Path,
        typer.Option(
            "--experiment",
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Dataset-independent offline experiment YAML.",
        ),
    ],
    dataset: Annotated[
        Path,
        typer.Option(
            "--dataset",
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Canonical immutable dataset manifest JSON.",
        ),
    ],
    mode: Annotated[
        RunMode,
        typer.Option("--mode", help="Only deterministic local execution is supported."),
    ] = RunMode.OFFLINE,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            file_okay=False,
            resolve_path=True,
            help="New immutable artifact directory (must not already exist).",
        ),
    ] = None,
) -> None:
    """Run baseline, bounded attack, top-three replay, and verified reporting."""
    if mode is not RunMode.OFFLINE:  # pragma: no cover - enum rejects other CLI values
        raise typer.BadParameter("only offline mode is available", param_hint="--mode")
    destination = output if output is not None else Path("artifacts") / f"{experiment.stem}-offline"
    try:
        result = run_offline_experiment(
            config_path=experiment,
            manifest_path=dataset,
            artifact_directory=destination,
        )
    except (OSError, OfflineRunError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo("status=verified")
    typer.echo("mode=offline")
    typer.echo(f"artifact_directory={destination.resolve()}")
    typer.echo(f"data_sha256={result.experiment.data_sha256}")
    typer.echo(f"config_sha256={result.config_sha256}")
    typer.echo(f"candidate_slots_consumed={result.candidate_slots_consumed}")
    typer.echo(f"top_failures={result.top_failure_count}")
    typer.echo(f"replayed={result.replay_count}")
    typer.echo(f"verified_failures={result.verified_failure_count}")
    typer.echo("artifacts=" + ",".join(offline_artifact_names()))


@research_app.command("run")
def run_research(
    experiment: Annotated[
        Path,
        typer.Option("--experiment", exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    dataset: Annotated[
        Path,
        typer.Option("--dataset", exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ],
    commission_bps: Annotated[float, typer.Option(min=0.0)] = 2.0,
    spread_bps: Annotated[float, typer.Option(min=0.0)] = 5.0,
    slippage_bps: Annotated[float, typer.Option(min=0.0)] = 3.0,
    initial_train_rows: Annotated[int, typer.Option(min=2)] = 40,
    test_rows: Annotated[int, typer.Option(min=1)] = 20,
    step_rows: Annotated[int, typer.Option(min=1)] = 20,
    regime_count: Annotated[int, typer.Option(min=2, max=8)] = 4,
) -> None:
    """Export one deterministic, provenance-bound MVP-1 research JSON artifact."""
    result_path = output / "research-result.json"
    try:
        config = load_offline_config(experiment)
        stored = LocalDatasetStore(dataset.parent.parent).validate(dataset)
        research_experiment = Experiment(
            experiment=config.bind_dataset(stored),
            costs=ExecutionCostAssumptions(
                commission_bps=commission_bps,
                spread_bps=spread_bps,
                slippage_bps=slippage_bps,
            ),
            walk_forward=WalkForwardConfig(
                initial_train_rows=initial_train_rows,
                test_rows=test_rows,
                step_rows=step_rows,
            ),
            regime=RegimeConfig(n_regimes=regime_count),
        )
        result = run_research_experiment(stored, research_experiment)
        output.mkdir(parents=True, exist_ok=False)
        result_path.write_bytes(result.model_dump_json(indent=2).encode("utf-8"))
    except (OSError, ValidationError, HistoricalDataError, OfflineRunError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo("status=verified")
    typer.echo(f"research_result={result_path}")
    typer.echo(f"data_sha256={stored.manifest.sha256}")
    typer.echo(f"seed={result.seed}")


@product_app.command("build-canonical")
def build_canonical_product(
    experiment: Annotated[
        Path,
        typer.Option("--experiment", exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    dataset: Annotated[
        Path,
        typer.Option("--dataset", exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    research_result: Annotated[
        Path,
        typer.Option(
            "--research-result", exists=True, dir_okay=False, readable=True, resolve_path=True
        ),
    ],
    output: Annotated[Path, typer.Option("--output", file_okay=False, resolve_path=True)],
    transaction_cost_bps: Annotated[float, typer.Option(min=0.0)] = 10.0,
) -> None:
    """Combine existing engine output with engine-verified deterministic AI findings."""
    destination = output / "canonical-product.json"
    try:
        config = load_offline_config(experiment)
        stored = LocalDatasetStore(dataset.parent.parent).validate(dataset)
        research = ExperimentResult.model_validate_json(research_result.read_bytes())
        artifact = build_canonical_product_artifact(
            stored,
            config.bind_dataset(stored),
            research,
            transaction_cost_bps=transaction_cost_bps,
        )
        output.mkdir(parents=True, exist_ok=False)
        destination.write_bytes(artifact.model_dump_json(indent=2).encode("utf-8"))
    except (OSError, ValidationError, HistoricalDataError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo("status=verified")
    typer.echo(f"canonical_product={destination}")
    typer.echo(f"ai_findings={len(artifact.ai_findings)}")


@demo_app.command("run")
def run_demo(
    experiment: Annotated[
        Path,
        typer.Option("--experiment", exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    dataset: Annotated[
        Path,
        typer.Option("--dataset", exists=True, dir_okay=False, readable=True, resolve_path=True),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", file_okay=False, resolve_path=True),
    ],
) -> None:
    """Run one bounded local-Ollama demo and export only verified telemetry."""
    try:
        result, telemetry = run_ollama_demo(
            config_path=experiment,
            manifest_path=dataset,
            output_directory=output,
        )
    except (DemoExportError, OfflineRunError, OSError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo("status=verified")
    typer.echo(f"demo_telemetry={(output / 'demo-telemetry.json').resolve()}")
    typer.echo(f"data_sha256={telemetry.dataset_manifest.sha256}")
    typer.echo(f"config_sha256={telemetry.config_sha256}")
    typer.echo(f"verified_failures={result.verified_failure_count}")


if __name__ == "__main__":  # pragma: no cover - console entry point is preferred
    app()
