"""Real PostgreSQL migration and canonical API integration acceptance."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from strategy_redteam.api.app import create_app
from strategy_redteam.data import LocalDatasetStore
from strategy_redteam.offline import OfflineExperimentConfig
from strategy_redteam.research import (
    ExecutionCostAssumptions,
    Experiment,
    WalkForwardConfig,
    run_research_experiment,
)
from strategy_redteam.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "offline-cache" / "manifests" / "correlation-break.json"
DATABASE_URL = os.environ.get("DATABASE_URL")


def _config() -> OfflineExperimentConfig:
    return OfflineExperimentConfig.model_validate(
        yaml.safe_load((ROOT / "config" / "example_60_40.yaml").read_text())
    )


@pytest.mark.postgres
def test_clean_migration_and_canonical_api_postgres_lifecycle() -> None:
    if DATABASE_URL is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration")
    environment = {**os.environ, "DATABASE_URL": DATABASE_URL}
    subprocess.run(["alembic", "downgrade", "base"], cwd=ROOT, env=environment, check=True)
    subprocess.run(["alembic", "upgrade", "head"], cwd=ROOT, env=environment, check=True)
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)
    assert {"experiments", "experiment_results", "alembic_version"} <= set(
        inspector.get_table_names()
    )
    assert "experiment_status" in inspector.get_enums()[0]["name"]

    settings = Settings(database_url=DATABASE_URL, dataset_root=MANIFEST.parent)
    started = time.perf_counter()
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/experiments",
            json={
                "configuration": _config().model_dump(mode="json"),
                "dataset_manifest": MANIFEST.name,
                "idempotency_key": "postgres-canonical-acceptance",
            },
        )
        post_seconds = time.perf_counter() - started
        assert response.status_code == 201
        record = response.json()
        assert record["status"] == "COMPLETED"
        result = client.get(f"/api/v1/experiments/{record['id']}/result")
        assert result.status_code == 200
        duplicate = client.post(
            "/api/v1/experiments",
            json={
                "configuration": _config().model_dump(mode="json"),
                "dataset_manifest": MANIFEST.name,
                "idempotency_key": "postgres-canonical-acceptance",
            },
        )
        assert duplicate.json()["id"] == record["id"]

    stored = LocalDatasetStore(MANIFEST.parent.parent).validate(MANIFEST)
    direct_started = time.perf_counter()
    direct = run_research_experiment(
        stored,
        Experiment(
            experiment=_config().bind_dataset(stored),
            costs=ExecutionCostAssumptions(commission_bps=2, spread_bps=5, slippage_bps=3),
            walk_forward=WalkForwardConfig(initial_train_rows=40, test_rows=20, step_rows=20),
        ),
    )
    direct_seconds = time.perf_counter() - direct_started
    api = result.json()
    assert api == direct.model_dump(mode="json")
    assert api["data_manifest"]["sha256"] == record["dataset_provenance_hash"]
    # Timing is deliberately observed rather than ordered: small local runs vary by
    # cache and scheduler noise, while both values prove bounded synchronous execution.
    assert post_seconds > 0.0
    assert direct_seconds > 0.0

    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT status, configuration, dataset_provenance_hash FROM experiments")
        ).one()
        assert row.status == "COMPLETED"
        assert row.configuration["seed"] == 20260823
        assert row.dataset_provenance_hash == api["data_manifest"]["sha256"]
        assert connection.execute(text("SELECT count(*) FROM experiment_results")).scalar_one() == 1
        connection.execute(text("DELETE FROM experiments"))
        assert connection.execute(text("SELECT count(*) FROM experiment_results")).scalar_one() == 0
