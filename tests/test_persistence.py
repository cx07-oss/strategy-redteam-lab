"""Lifecycle and relational persistence tests independent of the HTTP layer."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from strategy_redteam.data import LocalDatasetStore
from strategy_redteam.offline import OfflineExperimentConfig
from strategy_redteam.persistence.models import (
    Base,
    ExperimentRecord,
    ExperimentResultRecord,
    ExperimentStatus,
)
from strategy_redteam.persistence.repository import ExperimentRepository
from strategy_redteam.research import (
    ExecutionCostAssumptions,
    Experiment,
    ExperimentResult,
    WalkForwardConfig,
    run_research_experiment,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'persistence.db'}")
    event.listen(
        engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON")
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


def _record(key: str | None = "key") -> ExperimentRecord:
    return ExperimentRecord(
        idempotency_key=key,
        name="test",
        configuration={"seed": 7, "nested": {"value": "preserved"}},
        configuration_hash="a" * 64,
        seed=7,
        software_version="test-version",
    )


def _result() -> ExperimentResult:
    import yaml

    config = OfflineExperimentConfig.model_validate(
        yaml.safe_load((ROOT / "config" / "example_60_40.yaml").read_text())
    )
    manifest = (
        ROOT / "tests" / "fixtures" / "offline-cache" / "manifests" / "correlation-break.json"
    )
    stored = LocalDatasetStore(manifest.parent.parent).validate(manifest)
    return run_research_experiment(
        stored,
        Experiment(
            experiment=config.bind_dataset(stored),
            costs=ExecutionCostAssumptions(commission_bps=2, spread_bps=5, slippage_bps=3),
            walk_forward=WalkForwardConfig(initial_train_rows=40, test_rows=20, step_rows=20),
        ),
    )


def test_create_retrieve_list_lifecycle_and_metrics(session: Session) -> None:
    repository = ExperimentRepository(session)
    record = repository.create(_record())
    assert record.status is ExperimentStatus.PENDING
    assert repository.get(record.id) is record
    assert repository.get_by_key("key") is record
    records, total = repository.list(0, 20)
    assert records == [record] and total == 1
    assert record.configuration["nested"] == {"value": "preserved"}

    repository.mark_running(record)
    assert record.status is ExperimentStatus.RUNNING and record.started_at is not None
    record.dataset_provenance_hash = "b" * 64
    repository.complete(record, _result())
    assert record.status is ExperimentStatus.COMPLETED and record.completed_at is not None
    assert record.result is not None
    assert record.result.net_return == _result().costs.net_return
    assert record.result.structured_result["seed"] == _result().seed
    assert record.dataset_provenance_hash == "b" * 64


def test_failed_transition_key_uniqueness_and_foreign_key(session: Session) -> None:
    repository = ExperimentRepository(session)
    record = repository.create(_record())
    repository.mark_running(record)
    repository.fail(record, "execution_error", "safe message")
    assert record.status is ExperimentStatus.FAILED
    assert record.error_category == "execution_error"

    session.add(_record("key"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.add(
        ExperimentResultRecord(
            experiment_id="missing",
            gross_return=0,
            net_return=0,
            benchmark_return=0,
            sharpe=None,
            max_drawdown=None,
            turnover=None,
            total_cost=0,
            structured_result={},
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
