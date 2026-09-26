"""Fail-closed provider admission for presentation generation executions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from functools import wraps
from inspect import signature
from typing import Any, ParamSpec, TypeVar, cast

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceOperation
from services.generation_core.admission.strategy import resolve_generation_strategy
from services.presentation_profiles import (
    PresentationDriverId,
    PresentationProfileError,
    resolve_presentation_profile,
)
from services.provider_usage_instrumentation import public_operation_scope
from utils.llm_config import disable_thinking
from utils.llm_provider import generation_provider_policy

_Parameters = ParamSpec("_Parameters")
_Result = TypeVar("_Result")


def requested_generation_driver(
    request: GeneratePresentationRequest,
) -> PresentationDriverId:
    """Translate admitted compatibility fields, never a client driver field."""

    # A request can no longer name the template-v2 driver: the strategy
    # literal that produced it was retired with the Template V2 generation
    # entry, and the driver id itself was later removed from the registry in
    # ``services.presentation_profiles`` (2026-09-25), so a persisted deck
    # can no longer resolve to it either -- there is no remaining identity
    # this function needs to describe for it.
    if resolve_generation_strategy(request) == "svg":
        return "svg-v1"
    if request.template == "authored" or request.strategy == "authored":
        return "authored"
    raise PresentationProfileError("generation_strategy_not_supported")


def server_generation_profile_identity(
    request: GeneratePresentationRequest,
    persisted: PresentationModel | None,
) -> object | Mapping[str, Any]:
    """Resolve materialized identity or derive a blank staging shell profile."""

    requested_driver = requested_generation_driver(request)
    if persisted is not None:
        theme = persisted.theme if isinstance(persisted.theme, Mapping) else {}
        has_materialized_identity = bool(
            persisted.mode is not None
            or persisted.version == "v2-standard"
            or theme.get("mode") in {"authored", "template"}
        )
        if has_materialized_identity:
            profile = resolve_presentation_profile(persisted)
            if profile.driver_id != requested_driver:
                raise PresentationProfileError("generation_profile_conflict")
            return persisted

    identity_by_driver: dict[PresentationDriverId, Mapping[str, Any]] = {
        "svg-v1": {
            "generation_mode": "standard", "mode": "svg", "version": "v1-standard",
            "theme": {"mode": "svg"}, "profile_driver_id": "svg-v1", "profile_schema_version": 1,
        },
        "authored": {
            "generation_mode": "standard",
            "mode": "authored",
            "version": "v1-standard",
            "theme": {"mode": "authored"},
        },
    }
    return identity_by_driver[requested_driver]


def generation_provider_capabilities(
    request: GeneratePresentationRequest,
    driver_id: PresentationDriverId,
) -> tuple[str, ...]:
    required = ["structuredOutput"]
    if request.web_search:
        required.append("webSearch")
    if request.vision_qa:
        required.append("vision")
    if driver_id in {"authored", "svg-v1"} and not disable_thinking():
        required.append("thinking")
    return tuple(required)


def generation_provider_admitted(
    handler: Callable[_Parameters, Awaitable[_Result]],
) -> Callable[_Parameters, Awaitable[_Result]]:
    """Decorate a generation handler with task-scoped provider policy."""

    handler_signature = signature(handler)

    @wraps(handler)
    async def wrapped(*args: _Parameters.args, **kwargs: _Parameters.kwargs) -> _Result:
        bound = handler_signature.bind_partial(*args, **kwargs)
        request = cast(GeneratePresentationRequest, bound.arguments["request"])
        presentation_id = bound.arguments["presentation_id"]
        sql_session = cast(AsyncSession, bound.arguments["sql_session"])
        generation_job = bound.arguments.get("generation_job")
        public_operation_id = getattr(generation_job, "workspace_operation_id", None)
        public_attempt_token = getattr(generation_job, "attempt_token", None)
        public_fencing_token = None
        if public_operation_id is not None:
            public_fencing_token = await sql_session.scalar(
                select(WorkspaceOperation.fencing_token)
                .where(WorkspaceOperation.id == public_operation_id)
                .where(WorkspaceOperation.state == "running")
                .where(WorkspaceOperation.attempt_token == public_attempt_token)
                .execution_options(skip_owner_scope=True)
            )
        persisted = await sql_session.get(PresentationModel, presentation_id)
        try:
            identity = server_generation_profile_identity(request, persisted)
            profile = resolve_presentation_profile(identity)
            required = generation_provider_capabilities(request, profile.driver_id)
        except PresentationProfileError as error:
            raise HTTPException(status_code=422, detail=error.code) from error
        with public_operation_scope(
            public_operation_id,
            attempt_token=public_attempt_token,
            fencing_token=public_fencing_token,
        ):
            with generation_provider_policy(
                identity,
                required_capabilities=required,
            ):
                return await handler(*args, **kwargs)

    return wrapped


__all__ = [
    "generation_provider_admitted",
    "generation_provider_capabilities",
    "requested_generation_driver",
    "server_generation_profile_identity",
]
