"""Repository operations; no database concerns leak into the research engine."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from strategy_redteam.persistence.models import (
    ExperimentRecord,
    ExperimentResultRecord,
    ExperimentStatus,
)
from strategy_redteam.product import VerifiedHypothesis
from strategy_redteam.research import ExperimentResult


class ExperimentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        return self.session.get(ExperimentRecord, experiment_id)

    def get_by_key(self, key: str) -> ExperimentRecord | None:
        return self.session.scalar(
            select(ExperimentRecord).where(ExperimentRecord.idempotency_key == key)
        )

    def list(
        self,
        offset: int,
        limit: int,
        *,
        status: ExperimentStatus | None = None,
        search: str | None = None,
    ) -> tuple[list[ExperimentRecord], int]:
        conditions = []
        if status is not None:
            conditions.append(ExperimentRecord.status == status)
        if search:
            conditions.append(ExperimentRecord.name.ilike(f"%{search}%"))
        query = select(ExperimentRecord).where(*conditions)
        records = list(
            self.session.scalars(
                query.order_by(ExperimentRecord.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        total = self.session.scalar(
            select(func.count()).select_from(ExperimentRecord).where(*conditions)
        )
        return records, int(total or 0)

    def create(self, record: ExperimentRecord) -> ExperimentRecord:
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def mark_running(self, record: ExperimentRecord) -> None:
        record.status = ExperimentStatus.RUNNING
        record.started_at = datetime.now(UTC)
        self.session.commit()

    def complete(
        self,
        record: ExperimentRecord,
        result: ExperimentResult,
        findings: tuple[VerifiedHypothesis, ...] = (),
    ) -> None:
        payload = result.model_dump(mode="json")
        record.status = ExperimentStatus.COMPLETED
        record.completed_at = datetime.now(UTC)
        self.session.add(
            ExperimentResultRecord(
                experiment_id=record.id,
                gross_return=result.costs.gross_return,
                net_return=result.costs.net_return,
                benchmark_return=result.benchmark.benchmark_return,
                sharpe=result.performance.sharpe_ratio,
                max_drawdown=result.performance.maximum_drawdown,
                turnover=result.costs.turnover,
                total_cost=result.costs.total_trading_cost,
                structured_result=payload,
                ai_findings=[finding.model_dump(mode="json") for finding in findings],
            )
        )
        self.session.commit()

    def fail(self, record: ExperimentRecord, category: str, message: str) -> None:
        record.status = ExperimentStatus.FAILED
        record.completed_at = datetime.now(UTC)
        record.error_category, record.error_message = category, message[:500]
        self.session.commit()
