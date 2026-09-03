"""Public API request and response contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from strategy_redteam.offline import OfflineExperimentConfig
from strategy_redteam.persistence.models import ExperimentStatus
from strategy_redteam.research import ExperimentResult


class ExperimentCreateRequest(BaseModel):
    configuration: OfflineExperimentConfig
    dataset_manifest: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.json$")
    name: str | None = Field(default=None, max_length=128)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ExperimentResponse(BaseModel):
    id: str
    name: str | None
    status: ExperimentStatus
    seed: int
    dataset_provenance_hash: str | None
    error_category: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_record(cls, record: object) -> ExperimentResponse:
        return cls.model_validate(record, from_attributes=True)


class ExperimentListResponse(BaseModel):
    items: list[ExperimentResponse]
    offset: int
    limit: int
    total: int


class ErrorResponse(BaseModel):
    error: str
    message: str


ResearchResultResponse = ExperimentResult
