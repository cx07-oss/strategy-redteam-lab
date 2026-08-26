"""Gate 8 deterministic local/offline vertical-slice acceptance tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
from typer.testing import CliRunner

import strategy_redteam.offline as offline_module
from strategy_redteam import (
    MAX_CANDIDATES_PER_ROUND,
    MAX_ROUNDS,
    MAX_TOTAL_SCENARIOS,
    OFFLINE_REQUIRED_ARTIFACT_FILES,
    TOP_K,
    DefenderArtifact,
    DefenderVerdictValue,
    FailureReport,
    LocalDatasetStore,
    OfflineArtifactIntegrityError,
    OfflineReplayError,
    OfflineRunArtifact,
    RunTelemetry,
    ScenarioEvaluationRecord,
    verify_offline_artifacts,
)
from strategy_redteam.cli import app

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "example_60_40.yaml"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "offline-cache"
FIXTURE_MANIFEST = FIXTURE_ROOT / "manifests" / "correlation-break.json"
EXPECTED_ARTIFACTS = frozenset(
    {
        "attack/dataset_manifest.json",
        "attack/experiment.json",
        "attack/failure_report.md",
        "attack/policy.json",
        "attack/proposed_scenarios.jsonl",
        "attack/results.jsonl",
        "attack/top_failures.json",
        "defender_verdicts.json",
        "failure_report.json",
        "failure_report.md",
        "offline_run.json",
        "replay_results.jsonl",
        "telemetry.json",
    }
)


def _invoke_run(output: Path, config: Path = CONFIG_PATH, dataset: Path = FIXTURE_MANIFEST):
    return CliRunner().invoke(
        app,
        [
            "run",
            "--experiment",
            str(config),
            "--dataset",
            str(dataset),
            "--mode",
            "offline",
            "--output",
            str(output),
        ],
    )


def _artifact_names(directory: Path) -> frozenset[str]:
    return frozenset(
        path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()
    )


def _replays(directory: Path) -> tuple[ScenarioEvaluationRecord, ...]:
    return tuple(
        ScenarioEvaluationRecord.model_validate_json(line)
        for line in (directory / "replay_results.jsonl").read_bytes().splitlines()
    )


def test_fixture_has_known_correlation_break_and_higher_volatility() -> None:
    """The fixed path changes from negative correlation to a dated positive regime."""
    dataset = LocalDatasetStore(FIXTURE_ROOT).validate(FIXTURE_MANIFEST)
    closes = dataset.data.loc[:, pd.IndexSlice[:, "close"]].copy()
    closes.columns = closes.columns.droplevel("field")
    returns = closes.pct_change(fill_method=None).iloc[1:]
    quiet = returns.iloc[:40]
    broken = returns.iloc[40:]

    assert broken.index[0].date().isoformat() == "2024-02-28"
    assert quiet.corr().loc["SPY", "TLT"] < -0.99
    assert broken.corr().loc["SPY", "TLT"] > 0.99
    assert (broken.std() > quiet.std() * 4.0).all()


def test_offline_cli_produces_exact_verified_bundle_and_is_repeatable(
    tmp_path: Path,
) -> None:
    """One command runs the complete deterministic flow with continuous provenance."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_result = _invoke_run(first)
    second_result = _invoke_run(second)

    assert first_result.exit_code == 0, first_result.output
    assert second_result.exit_code == 0, second_result.output
    assert "status=verified" in first_result.output
    assert OFFLINE_REQUIRED_ARTIFACT_FILES == EXPECTED_ARTIFACTS
    assert _artifact_names(first) == EXPECTED_ARTIFACTS
    assert _artifact_names(second) == EXPECTED_ARTIFACTS
    for name in EXPECTED_ARTIFACTS:
        assert (first / Path(name)).read_bytes() == (second / Path(name)).read_bytes()

    index = verify_offline_artifacts(first)
    assert isinstance(index, OfflineRunArtifact)
    assert index.verification_status == "verified"
    assert index.budget.model_dump(mode="python") == {
        "schema_version": "1.0",
        "max_rounds": MAX_ROUNDS,
        "max_candidates_per_round": MAX_CANDIDATES_PER_ROUND,
        "max_total_scenarios": MAX_TOTAL_SCENARIOS,
        "top_k": TOP_K,
        "hard_max_rounds": MAX_ROUNDS,
        "hard_max_candidates_per_round": MAX_CANDIDATES_PER_ROUND,
        "hard_max_total_scenarios": MAX_TOTAL_SCENARIOS,
        "hard_top_k": TOP_K,
    }
    assert index.candidate_slots_consumed == MAX_CANDIDATES_PER_ROUND
    assert index.top_failure_count == TOP_K
    assert index.replay_count == TOP_K
    assert index.verified_failure_count == TOP_K

    manifest = json.loads((first / "attack" / "dataset_manifest.json").read_text())
    attack_index = json.loads((first / "attack" / "experiment.json").read_text())
    report = FailureReport.model_validate_json((first / "failure_report.json").read_bytes())
    defender = DefenderArtifact.model_validate_json((first / "defender_verdicts.json").read_bytes())
    replays = _replays(first)
    telemetry = RunTelemetry.model_validate_json((first / "telemetry.json").read_bytes())
    assert telemetry.schema_version == "1.0"
    assert telemetry.dataset_manifest_sha256 == index.dataset_manifest_sha256
    assert telemetry.baseline_metrics == report.baseline_metrics
    assert telemetry.evaluations == tuple(
        ScenarioEvaluationRecord.model_validate_json(line)
        for line in (first / "attack" / "results.jsonl").read_bytes().splitlines()
    )
    assert tuple(event.sequence for event in telemetry.events) == tuple(
        range(1, len(telemetry.events) + 1)
    )
    assert "secret" not in (first / "telemetry.json").read_text(encoding="utf-8").lower()
    assert manifest["sha256"] == index.experiment.data_sha256
    assert attack_index["experiment"]["data_sha256"] == index.experiment.data_sha256
    assert report.data_sha256 == index.experiment.data_sha256
    assert {item.result.data_sha256 for item in replays} == {index.experiment.data_sha256}
    for replay in replays:
        assert replay.chart_points
        assert replay.chart_points[0].date.isoformat() == "2024-01-02"
        assert replay.chart_points[-1].date > replay.chart_points[0].date
        assert all(
            point.baseline_equity > 0.0 and point.stressed_equity > 0.0
            for point in replay.chart_points
        )
        breach_dates = {breach.onset_date for breach in replay.result.breaches}
        assert breach_dates.issubset({point.date for point in replay.chart_points})
        volatility = replay.component_summaries[0]
        correlation = replay.component_summaries[1]
        assert all(
            after.sample_log_return_std is not None
            and before.sample_log_return_std is not None
            and after.sample_log_return_std > before.sample_log_return_std
            for before, after in zip(
                volatility.pre_transform_summary.assets,
                volatility.post_transform_summary.assets,
                strict=True,
            )
        )
        assert correlation.pre_transform_summary.spy_tlt_correlation is not None
        assert correlation.post_transform_summary.spy_tlt_correlation is not None
        assert correlation.pre_transform_summary.spy_tlt_correlation < 0.0
        assert correlation.post_transform_summary.spy_tlt_correlation > 0.0
    assert all(
        verdict.verdict is DefenderVerdictValue.REPRODUCED
        and verdict.data_hash_matches
        and verdict.config_hash_matches
        and verdict.result_matches
        and verdict.event_dates_match
        and verdict.transform_hash_matches
        for verdict in defender.verdicts
    )

    assert report.notice == "Research only; not investment advice."
    assert report.baseline_metrics is not None
    assert len(report.verified_results) == TOP_K
    assert report.summary == "Deterministic defence reproduced 3 of 3 bounded attacker failures."
    assert tuple(result.scenario_id for result in report.verified_results) == (
        "offline-r01-c06",
        "offline-r01-c05",
        "offline-r01-c04",
    )
    for result in report.verified_results:
        onsets = {breach.rule_id: breach.onset_date.isoformat() for breach in result.breaches}
        assert onsets == {
            "drawdown-limit": "2024-03-11",
            "rolling-loss-limit": "2024-03-07",
            "volatility-multiple-limit": "2024-01-30",
        }
        explanation = report.scenario_explanations[result.scenario_id]
        assert "volatility changed" in explanation
        assert "innovation correlation became positive" in explanation
        assert "Both sleeves contributed negatively" in explanation
        assert "first breached on" in explanation
        assert "Independent deterministic replay reproduced this chain" in explanation

    markdown = (first / "failure_report.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Defender failure report")
    assert "Explicit numeric stress" in markdown
    assert "matching hashes, code/config version, event dates, and metrics" in markdown
    assert "Sharpe" not in markdown


def test_cli_returns_nonzero_for_schema_failure(tmp_path: Path) -> None:
    """Unknown configuration fields fail before any artifact directory is published."""
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        CONFIG_PATH.read_text(encoding="utf-8") + "unknown_field: rejected\n",
        encoding="utf-8",
    )
    output = tmp_path / "schema-failure"

    result = _invoke_run(output, config=invalid)

    assert result.exit_code != 0
    assert "schema validation failed" in result.output
    assert not output.exists()


def test_cli_returns_nonzero_for_dataset_hash_failure(tmp_path: Path) -> None:
    """A changed Parquet byte is rejected before baseline calculation."""
    copied_store = tmp_path / "offline-cache"
    shutil.copytree(FIXTURE_ROOT, copied_store)
    manifest_path = copied_store / "manifests" / "correlation-break.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_path = copied_store / "datasets" / f"{manifest['sha256']}.parquet"
    changed = bytearray(dataset_path.read_bytes())
    changed[-1] ^= 1
    dataset_path.write_bytes(changed)
    output = tmp_path / "hash-failure"

    result = _invoke_run(output, dataset=manifest_path)

    assert result.exit_code != 0
    assert "SHA-256 does not match" in result.output
    assert not output.exists()


def test_cli_returns_nonzero_for_replay_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A defender replay error prevents final artifact publication."""

    def reject_replay(*args, **kwargs):
        del args, kwargs
        raise OfflineReplayError("forced replay failure")

    monkeypatch.setattr(offline_module.DefenderService, "defend", reject_replay)
    output = tmp_path / "replay-failure"

    result = _invoke_run(output)

    assert result.exit_code != 0
    assert "forced replay failure" in result.output
    assert not output.exists()


def test_cli_returns_nonzero_for_incomplete_artifact_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Final exact-membership verification is mandatory before publication."""

    def reject_incomplete(directory: Path):
        del directory
        raise OfflineArtifactIntegrityError("forced incomplete artifact failure")

    monkeypatch.setattr(offline_module, "verify_offline_artifacts", reject_incomplete)
    output = tmp_path / "incomplete-failure"

    result = _invoke_run(output)

    assert result.exit_code != 0
    assert "forced incomplete artifact failure" in result.output
    assert not output.exists()
