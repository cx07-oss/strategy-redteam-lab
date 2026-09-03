"""Minimal relational records for experiment lifecycle and queryable outcomes."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


JSONValue = JSON().with_variant(JSONB, "postgresql")


class ExperimentStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExperimentRecord(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(ExperimentStatus, name="experiment_status"),
        nullable=False,
        default=ExperimentStatus.PENDING,
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONValue, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[int] = mapped_column(nullable=False)
    dataset_provenance_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    software_version: Mapped[str] = mapped_column(String(128), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[ExperimentResultRecord | None] = relationship(
        back_populates="experiment", uselist=False
    )


class ExperimentResultRecord(Base):
    __tablename__ = "experiment_results"

    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True
    )
    gross_return: Mapped[float] = mapped_column(nullable=False)
    net_return: Mapped[float] = mapped_column(nullable=False)
    benchmark_return: Mapped[float] = mapped_column(nullable=False)
    sharpe: Mapped[float | None] = mapped_column(nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(nullable=True)
    turnover: Mapped[float | None] = mapped_column(nullable=True)
    total_cost: Mapped[float] = mapped_column(nullable=False)
    structured_result: Mapped[dict[str, Any]] = mapped_column(JSONValue, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    experiment: Mapped[ExperimentRecord] = relationship(back_populates="result")
