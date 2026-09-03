"""Public API request and response contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from strategy_redteam.domain import DataManifest
from strategy_redteam.offline import OfflineExperimentConfig
from strategy_redteam.persistence.models import ExperimentRecord, ExperimentStatus
from strategy_redteam.product import HistoricalEvent, VerifiedHypothesis
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
    configuration_hash: str
    software_version: str
    strategy_id: str
    dataset_id: str | None
    net_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    robustness_summary: str | None

    @classmethod
    def from_record(cls, record: ExperimentRecord) -> ExperimentResponse:
        result = record.result
        dataset_id = None
        if result is not None:
            dataset_id = result.structured_result.get("data_manifest", {}).get("dataset_id")
        strategy = record.configuration.get("strategy", {})
        worst_stress = None
        if result is not None:
            points = result.structured_result.get("stress_surface", [])
            if points:
                baseline = result.net_return
                worst_stress = baseline - min(float(point["result"]) for point in points)
        return cls(
            id=record.id,
            name=record.name,
            status=record.status,
            seed=record.seed,
            dataset_provenance_hash=record.dataset_provenance_hash,
            error_category=record.error_category,
            error_message=record.error_message,
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            configuration_hash=record.configuration_hash,
            software_version=record.software_version,
            strategy_id=str(strategy.get("strategy_id", "unknown")),
            dataset_id=dataset_id,
            net_return=None if result is None else result.net_return,
            sharpe=None if result is None else result.sharpe,
            max_drawdown=None if result is None else result.max_drawdown,
            robustness_summary=(
                None
                if worst_stress is None
                else f"Worst stress degradation {worst_stress:.4f}"
            ),
        )


class ExperimentListResponse(BaseModel):
    items: list[ExperimentResponse]
    offset: int
    limit: int
    total: int


class ErrorResponse(BaseModel):
    error: str
    message: str


ResearchResultResponse = ExperimentResult


class DatasetOption(BaseModel):
    manifest_name: str
    label: str
    canonical: bool
    manifest: DataManifest


class ConfigurationOption(BaseModel):
    configuration_id: str
    label: str
    configuration: OfflineExperimentConfig


class ProductCatalogResponse(BaseModel):
    datasets: list[DatasetOption]
    configurations: list[ConfigurationOption]
    seeds: list[int]
    provider_modes: list[str]
    historical_events: list[HistoricalEvent]


class HypothesisFindingsResponse(BaseModel):
    experiment_id: str
    provider_mode: str = "deterministic"
    findings: list[VerifiedHypothesis]


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_ids: list[str] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def unique_ids(self) -> CompareRequest:
        if len(self.experiment_ids) != len(set(self.experiment_ids)):
            raise ValueError("experiment_ids must be unique")
        return self


class ComparisonSummary(BaseModel):
    experiment_id: str
    name: str | None
    net_return: float
    benchmark_excess: float
    sharpe: float | None
    max_drawdown: float | None
    turnover: float
    total_cost: float
    oos_return: float
    worst_regime: int | None
    worst_regime_return: float | None
    worst_stress_degradation: float
    reproduced_hypotheses: int
    total_hypotheses: int


class ComparisonResponse(BaseModel):
    items: list[ComparisonSummary]
