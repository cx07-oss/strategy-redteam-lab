"""Focused HTTP acceptance tests for the thin MVP 2 API boundary."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from strategy_redteam.api.app import create_app
from strategy_redteam.persistence.models import Base
from strategy_redteam.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_ROOT = ROOT / "tests" / "fixtures" / "offline-cache" / "manifests"


def _configuration() -> dict[str, object]:
    return yaml.safe_load((ROOT / "config" / "example_60_40.yaml").read_text())


def _client(tmp_path: Path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    app = create_app(Settings(database_url=database_url, dataset_root=MANIFEST_ROOT))
    return TestClient(app)


def _request(key: str = "api-test-key") -> dict[str, object]:
    return {
        "configuration": _configuration(),
        "dataset_manifest": "correlation-break.json",
        "idempotency_key": key,
        "name": "canonical API acceptance",
    }


def test_health_ready_and_experiment_lifecycle(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    client = _client(tmp_path)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json() == {"status": "ready"}

    created = client.post("/api/v1/experiments", json=_request())
    assert created.status_code == 201
    experiment = created.json()
    assert experiment["status"] == "COMPLETED"
    assert len(experiment["dataset_provenance_hash"]) == 64

    listed = client.get("/api/v1/experiments")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == experiment["id"]
    assert client.get(f"/api/v1/experiments/{experiment['id']}").json() == experiment

    result = client.get(f"/api/v1/experiments/{experiment['id']}/result")
    assert result.status_code == 200
    assert result.json()["experiment_id"] == _configuration()["experiment_id"]
    assert len(result.json()["walk_forward"]) == 2
    assert len(result.json()["stress_surface"]) == 4

    duplicate = client.post("/api/v1/experiments", json=_request())
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == experiment["id"]
    assert client.get("/api/v1/experiments").json()["total"] == 1
    assert "request_id=" in caplog.text
    assert "route=/api/v1/experiments status=201 duration_ms=" in caplog.text
    assert "research_duration_ms=" in caplog.text
    assert "walk_forward_fold_count=2" in caplog.text
    assert "regime_count=" in caplog.text


def test_invalid_and_unknown_requests_use_safe_error_contract(tmp_path: Path) -> None:
    client = _client(tmp_path)
    malformed = client.post("/api/v1/experiments", json={"dataset_manifest": "x.json"})
    assert malformed.status_code == 422
    assert malformed.json() == {
        "error": "validation_error",
        "message": "Request validation failed",
    }

    invalid = _request("invalid-config")
    invalid["configuration"] = {**_configuration(), "seed": -1}
    response = client.post("/api/v1/experiments", json=invalid)
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"

    for suffix in ("", "/result"):
        response = client.get(f"/api/v1/experiments/does-not-exist{suffix}")
        assert response.status_code == 404
        assert response.json() == {"error": "request_error", "message": "Experiment not found"}


def test_controlled_research_failure_is_persisted_and_not_exposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import strategy_redteam.experiment_service as service_module

    def fail(*_: object, **__: object) -> Any:
        raise RuntimeError("internal stack detail")

    monkeypatch.setattr(service_module, "run_research_experiment", fail)
    caplog.set_level(logging.ERROR)
    client = _client(tmp_path)
    response = client.post("/api/v1/experiments", json=_request("failure-key"))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error_category"] == "execution_error"
    assert body["error_message"] == "Research execution failed"
    assert "internal stack detail" not in response.text
    assert "research_failed" in caplog.text
    assert "internal stack detail" in caplog.text


def test_product_catalog_findings_filters_and_comparison(tmp_path: Path) -> None:
    client = _client(tmp_path)
    catalog = client.get("/api/v1/catalog")
    assert catalog.status_code == 200
    assert {item["manifest_name"] for item in catalog.json()["datasets"]} == {
        "correlation-break.json",
        "spy-tlt-2007-2025.json",
    }
    assert catalog.json()["provider_modes"] == ["deterministic"]

    first = client.post("/api/v1/experiments", json=_request("compare-one")).json()
    second_request = _request("compare-two")
    second_request["name"] = "comparison control"
    second = client.post("/api/v1/experiments", json=second_request).json()
    findings = client.get(f"/api/v1/experiments/{first['id']}/ai-findings")
    assert findings.status_code == 200
    assert len(findings.json()["findings"]) == 3
    assert {item["verification_status"] for item in findings.json()["findings"]} == {
        "reproduced",
        "rejected",
    }

    comparison = client.post(
        "/api/v1/experiments/compare",
        json={"experiment_ids": [first["id"], second["id"]]},
    )
    assert comparison.status_code == 200
    assert len(comparison.json()["items"]) == 2
    assert all(item["total_hypotheses"] == 3 for item in comparison.json()["items"])
    filtered = client.get("/api/v1/experiments?status=COMPLETED&search=control")
    assert filtered.json()["total"] == 1
