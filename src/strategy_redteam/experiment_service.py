"""Application service coordinating immutable research execution and persistence."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from sqlalchemy.orm import Session

from strategy_redteam.data import LocalDatasetStore
from strategy_redteam.offline import OfflineExperimentConfig
from strategy_redteam.persistence.models import ExperimentRecord
from strategy_redteam.persistence.repository import ExperimentRepository
from strategy_redteam.research import (
    ExecutionCostAssumptions,
    Experiment,
    ExperimentResult,
    WalkForwardConfig,
    run_research_experiment,
)

logger = logging.getLogger(__name__)


class ExperimentInputError(ValueError):
    pass


def _safe_manifest(dataset_root: Path, manifest_name: str) -> Path:
    candidate = (dataset_root / manifest_name).resolve()
    root = dataset_root.resolve()
    if candidate.parent != root or candidate.suffix != ".json" or not candidate.is_file():
        raise ExperimentInputError("dataset manifest is not available")
    return candidate


class ExperimentService:
    def __init__(self, session: Session, dataset_root: Path) -> None:
        self.repository = ExperimentRepository(session)
        self.dataset_root = dataset_root

    def submit(
        self,
        configuration: OfflineExperimentConfig,
        manifest_name: str,
        idempotency_key: str | None,
        name: str | None,
    ) -> tuple[ExperimentRecord, bool]:
        if idempotency_key and (existing := self.repository.get_by_key(idempotency_key)):
            return existing, True
        manifest = _safe_manifest(self.dataset_root, manifest_name)
        config_payload = configuration.model_dump(mode="json")
        config_hash = hashlib.sha256(configuration.model_dump_json().encode()).hexdigest()
        record = self.repository.create(
            ExperimentRecord(
                idempotency_key=idempotency_key,
                name=name,
                configuration=config_payload,
                configuration_hash=config_hash,
                seed=configuration.seed,
                software_version=configuration.code_version,
            )
        )
        started = time.perf_counter()
        try:
            self.repository.mark_running(record)
            stored = LocalDatasetStore(manifest.parent.parent).validate(manifest)
            record.dataset_provenance_hash = stored.manifest.sha256
            self.repository.session.commit()
            result = run_research_experiment(
                stored,
                Experiment(
                    experiment=configuration.bind_dataset(stored),
                    costs=ExecutionCostAssumptions(
                        commission_bps=2.0, spread_bps=5.0, slippage_bps=3.0
                    ),
                    walk_forward=WalkForwardConfig(
                        initial_train_rows=40, test_rows=20, step_rows=20
                    ),
                ),
            )
            self.repository.complete(record, result)
            logger.info(
                "research_completed experiment_id=%s research_duration_ms=%d "
                "walk_forward_fold_count=%d stress_scenario_count=%d regime_count=%d "
                "result_status=COMPLETED",
                record.id,
                (time.perf_counter() - started) * 1000,
                len(result.walk_forward),
                len(result.stress_surface),
                len(result.regime_summaries),
            )
        except Exception as error:
            logger.exception("research_failed experiment_id=%s", record.id)
            category = (
                "validation_error"
                if isinstance(error, (ValueError, ExperimentInputError))
                else "execution_error"
            )
            self.repository.fail(
                record,
                category,
                "Research execution failed" if category == "execution_error" else str(error),
            )
        self.repository.session.refresh(record)
        return record, False

    def result(self, record: ExperimentRecord) -> ExperimentResult | None:
        return (
            None
            if record.result is None
            else ExperimentResult.model_validate(record.result.structured_result)
        )
