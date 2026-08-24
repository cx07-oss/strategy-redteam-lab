"""Foundry Invocations protocol host shared by attacker and defender entry points."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from strategy_redteam.domain import ContractModel
from strategy_redteam.hosted import (
    ArtifactStoreError,
    DatasetStoreError,
    HostedApplicationError,
)

MAX_INVOCATION_BYTES = 4_194_304
logger = logging.getLogger(__name__)

RequestModel = TypeVar("RequestModel", bound=ContractModel)
ResponseModel = TypeVar("ResponseModel", bound=ContractModel)
RequestModelContravariant = TypeVar(
    "RequestModelContravariant", bound=ContractModel, contravariant=True
)
ResponseModelCovariant = TypeVar(
    "ResponseModelCovariant", bound=ContractModel, covariant=True
)


class HostedInvoker(Protocol[RequestModelContravariant, ResponseModelCovariant]):
    def invoke(
        self, request: RequestModelContravariant
    ) -> ResponseModelCovariant: ...


def _openapi_spec(
    *,
    title: str,
    request_model: type[Any],
    response_model: type[Any],
) -> dict[str, object]:
    components: dict[str, object] = {}
    for model in (request_model, response_model):
        schema = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        definitions = schema.pop("$defs", {})
        for name, definition in definitions.items():
            existing = components.get(name)
            if existing is not None and existing != definition:
                raise ValueError(f"conflicting OpenAPI component schema: {name}")
            components[name] = definition
        components[model.__name__] = schema
    return {
        "openapi": "3.0.3",
        "info": {"title": title, "version": "1.0.0"},
        "paths": {
            "/invocations": {
                "post": {
                    "operationId": "invoke",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": (
                                        "#/components/schemas/"
                                        f"{request_model.__name__}"
                                    )
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Typed bounded result",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": (
                                            "#/components/schemas/"
                                            f"{response_model.__name__}"
                                        )
                                    }
                                }
                            },
                        },
                        "413": {"description": "Invocation payload too large"},
                        "422": {"description": "Invalid typed request"},
                    },
                }
            }
        },
        "components": {"schemas": components},
    }


def create_invocation_host(
    *,
    title: str,
    request_model: type[RequestModel],
    response_model: type[ResponseModel],
    application_factory: Callable[[], HostedInvoker[RequestModel, ResponseModel]],
    enable_observability: bool = True,
) -> InvocationAgentServerHost:
    """Create the documented /readiness and POST /invocations ASGI host."""
    openapi_spec = _openapi_spec(
        title=title,
        request_model=request_model,
        response_model=response_model,
    )
    host = (
        InvocationAgentServerHost(openapi_spec=openapi_spec)
        if enable_observability
        else InvocationAgentServerHost(
            openapi_spec=openapi_spec,
            configure_observability=None,
        )
    )
    application: HostedInvoker[RequestModel, ResponseModel] | None = None

    @host.invoke_handler
    async def invoke(request: Request) -> Response:
        nonlocal application
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_INVOCATION_BYTES:
                    return JSONResponse(
                        {"error": {"code": "payload_too_large"}}, status_code=413
                    )
            except ValueError:
                return JSONResponse(
                    {"error": {"code": "invalid_content_length"}}, status_code=400
                )
        body = await request.body()
        if len(body) > MAX_INVOCATION_BYTES:
            return JSONResponse(
                {"error": {"code": "payload_too_large"}}, status_code=413
            )
        try:
            typed_request = request_model.model_validate_json(body)
        except ValidationError:
            return JSONResponse(
                {"error": {"code": "invalid_request_contract"}}, status_code=422
            )
        try:
            if application is None:
                application = application_factory()
            result = await asyncio.to_thread(application.invoke, typed_request)
        except DatasetStoreError:
            return JSONResponse(
                {"error": {"code": "immutable_dataset_verification_failed"}},
                status_code=409,
            )
        except ArtifactStoreError:
            return JSONResponse(
                {"error": {"code": "immutable_artifact_publication_failed"}},
                status_code=409,
            )
        except HostedApplicationError:
            return JSONResponse(
                {"error": {"code": "hosted_application_rejected_request"}},
                status_code=422,
            )
        except Exception:
            logger.exception("hosted invocation failed without logging request data")
            return JSONResponse(
                {"error": {"code": "internal_error"}}, status_code=500
            )
        return JSONResponse(result.model_dump(mode="json"))

    return host
