"""FastAPI factory; routes delegate to the application service."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from strategy_redteam.api.schemas import (
    CompareRequest,
    ComparisonResponse,
    ComparisonSummary,
    ConfigurationOption,
    DatasetOption,
    ErrorResponse,
    ExperimentCreateRequest,
    ExperimentListResponse,
    ExperimentResponse,
    HypothesisFindingsResponse,
    ProductCatalogResponse,
    ResearchResultResponse,
)
from strategy_redteam.data import LocalDatasetStore
from strategy_redteam.experiment_service import ExperimentInputError, ExperimentService
from strategy_redteam.offline import load_offline_config
from strategy_redteam.persistence.database import build_engine, build_session_factory
from strategy_redteam.persistence.models import ExperimentStatus
from strategy_redteam.persistence.repository import ExperimentRepository
from strategy_redteam.product import HISTORICAL_EVENTS, AIProviderMode, VerifiedHypothesis
from strategy_redteam.research import ExperimentResult
from strategy_redteam.settings import Settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings()
    engine = build_engine(configured.database_url)
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="Strategy Red Team Research API", version="1.0", lifespan=lifespan)
    app.state.session_factory, app.state.engine, app.state.settings = factory, engine, configured
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[x for x in configured.cors_origins.split(",") if x],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_logging(request: Request, call_next: object) -> object:
        started, request_id = (
            time.perf_counter(),
            request.headers.get("X-Request-ID", str(uuid.uuid4())),
        )
        response = await call_next(request)  # type: ignore[operator]
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request request_id=%s route=%s status=%d duration_ms=%d",
            request_id,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="validation_error", message="Request validation failed"
            ).model_dump(),
        )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        """Keep expected client errors in the public, traceback-free error contract."""
        message = error.detail if isinstance(error.detail, str) else "Request failed"
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorResponse(error="request_error", message=message).model_dump(),
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_: Request, __: SQLAlchemyError) -> JSONResponse:
        logger.exception("database_failure")
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error="database_error", message="Database unavailable"
            ).model_dump(),
        )

    def get_session() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": configured.app_env}

    @app.get("/ready", tags=["operations"], responses={503: {"model": ErrorResponse}})
    def ready(session: Session = Depends(get_session)) -> dict[str, str]:
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            raise HTTPException(status_code=503, detail="Database unavailable") from error
        return {"status": "ready"}

    @app.post(
        "/api/v1/experiments",
        response_model=ExperimentResponse,
        status_code=201,
        tags=["experiments"],
    )
    def create(
        request: ExperimentCreateRequest, session: Session = Depends(get_session)
    ) -> ExperimentResponse:
        try:
            record, duplicate = ExperimentService(
                session, configured.dataset_root, configured.canonical_dataset_root
            ).submit(
                request.configuration,
                request.dataset_manifest,
                request.idempotency_key,
                request.name,
            )
        except ExperimentInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        response = ExperimentResponse.from_record(record)
        if duplicate:
            return response
        return response

    @app.get("/api/v1/experiments", response_model=ExperimentListResponse, tags=["experiments"])
    def list_experiments(
        offset: int = 0,
        limit: int = 20,
        status: ExperimentStatus | None = None,
        search: str | None = None,
        session: Session = Depends(get_session),
    ) -> ExperimentListResponse:
        if offset < 0 or not 1 <= limit <= 100 or (search is not None and len(search) > 128):
            raise HTTPException(status_code=422, detail="offset must be >= 0 and limit 1..100")
        records, total = ExperimentRepository(session).list(
            offset, limit, status=status, search=search
        )
        return ExperimentListResponse(
            items=[ExperimentResponse.from_record(record) for record in records],
            offset=offset,
            limit=limit,
            total=total,
        )

    @app.get("/api/v1/catalog", response_model=ProductCatalogResponse, tags=["product"])
    def product_catalog() -> ProductCatalogResponse:
        datasets: list[DatasetOption] = []
        roots = (configured.dataset_root, configured.canonical_dataset_root)
        for root in roots:
            if not root.exists():
                continue
            for manifest_path in sorted(root.glob("*.json")):
                stored = LocalDatasetStore(root.parent).validate(manifest_path)
                canonical = manifest_path.name == "spy-tlt-2007-2025.json"
                datasets.append(
                    DatasetOption(
                        manifest_name=manifest_path.name,
                        label=(
                            "SPY / TLT 2007-2025 (historical)"
                            if canonical
                            else "Synthetic correlation-break fixture"
                        ),
                        canonical=canonical,
                        manifest=stored.manifest,
                    )
                )
        configurations = []
        for config_id, label, relative in (
            ("canonical-ci", "Canonical deterministic fixture", "config/example_60_40.yaml"),
            ("historical-60-40", "Historical monthly 60/40", "config/historical_60_40.yaml"),
        ):
            path = configured.configuration_root / Path(relative).name
            if path.exists():
                configurations.append(
                    ConfigurationOption(
                        configuration_id=config_id,
                        label=label,
                        configuration=load_offline_config(path),
                    )
                )
        return ProductCatalogResponse(
            datasets=datasets,
            configurations=configurations,
            seeds=[20260823],
            provider_modes=[AIProviderMode.DETERMINISTIC.value],
            historical_events=list(HISTORICAL_EVENTS),
        )

    @app.post(
        "/api/v1/experiments/compare",
        response_model=ComparisonResponse,
        tags=["experiments"],
    )
    def compare_experiments(
        request: CompareRequest, session: Session = Depends(get_session)
    ) -> ComparisonResponse:
        repository = ExperimentRepository(session)
        summaries: list[ComparisonSummary] = []
        for experiment_id in request.experiment_ids:
            record = repository.get(experiment_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Experiment not found")
            if record.status is not ExperimentStatus.COMPLETED or record.result is None:
                raise HTTPException(status_code=409, detail="Experiment result is not available")
            result = ExperimentResult.model_validate_json(
                json.dumps(record.result.structured_result)
            )
            worst_regime = min(
                result.regime_summaries, key=lambda item: item.strategy_return, default=None
            )
            worst_stress = min(
                (item.result for item in result.stress_surface),
                default=result.costs.net_return,
            )
            findings = [
                VerifiedHypothesis.model_validate_json(json.dumps(item))
                for item in record.result.ai_findings
            ]
            summaries.append(
                ComparisonSummary(
                    experiment_id=record.id,
                    name=record.name,
                    net_return=result.costs.net_return,
                    benchmark_excess=result.benchmark.excess_return,
                    sharpe=result.performance.sharpe_ratio,
                    max_drawdown=result.performance.maximum_drawdown,
                    turnover=result.costs.turnover,
                    total_cost=result.costs.total_trading_cost,
                    oos_return=result.walk_forward_out_of_sample.total_return,
                    worst_regime=None if worst_regime is None else worst_regime.regime,
                    worst_regime_return=(
                        None if worst_regime is None else worst_regime.strategy_return
                    ),
                    worst_stress_degradation=result.costs.net_return - worst_stress,
                    reproduced_hypotheses=sum(
                        finding.verification_status.value == "reproduced" for finding in findings
                    ),
                    total_hypotheses=len(findings),
                )
            )
        return ComparisonResponse(items=summaries)

    @app.get(
        "/api/v1/experiments/{experiment_id}",
        response_model=ExperimentResponse,
        tags=["experiments"],
    )
    def get_experiment(
        experiment_id: str, session: Session = Depends(get_session)
    ) -> ExperimentResponse:
        record = ExperimentRepository(session).get(experiment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        return ExperimentResponse.from_record(record)

    @app.get(
        "/api/v1/experiments/{experiment_id}/ai-findings",
        response_model=HypothesisFindingsResponse,
        tags=["experiments"],
    )
    def get_ai_findings(
        experiment_id: str, session: Session = Depends(get_session)
    ) -> HypothesisFindingsResponse:
        record = ExperimentRepository(session).get(experiment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        if record.status is not ExperimentStatus.COMPLETED or record.result is None:
            raise HTTPException(status_code=409, detail="Experiment result is not available")
        return HypothesisFindingsResponse(
            experiment_id=record.id,
            findings=[
                VerifiedHypothesis.model_validate_json(json.dumps(item))
                for item in record.result.ai_findings
            ],
        )

    @app.get(
        "/api/v1/experiments/{experiment_id}/result",
        response_model=ResearchResultResponse,
        tags=["experiments"],
    )
    def get_result(
        experiment_id: str, session: Session = Depends(get_session)
    ) -> ResearchResultResponse:
        record = ExperimentRepository(session).get(experiment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
        if record.status is not ExperimentStatus.COMPLETED or record.result is None:
            raise HTTPException(status_code=409, detail="Experiment result is not available")
        # JSONB stores JSON-compatible dates as strings; validate through Pydantic's
        # JSON mode to restore the typed MVP 1 result without changing its payload.
        return ResearchResultResponse.model_validate_json(
            json.dumps(record.result.structured_result)
        )

    return app


app = create_app()
