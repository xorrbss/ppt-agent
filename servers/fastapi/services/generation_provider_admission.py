"""Fail-closed provider admission for presentation generation executions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from functools import wraps
from inspect import signature
from typing import Any, ParamSpec, TypeVar, cast

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.presentation import PresentationModel
from services.presentation_profiles import (
    PresentationDriverId,
    PresentationProfileError,
    resolve_presentation_profile,
)
from utils.llm_config import disable_thinking
from utils.llm_provider import generation_provider_policy

_Parameters = ParamSpec("_Parameters")
_Result = TypeVar("_Result")


def requested_generation_driver(
    request: GeneratePresentationRequest,
) -> PresentationDriverId:
    """Translate admitted compatibility fields, never a client driver field."""

    if request.strategy == "template_v2":
        return "template-v2"
    if request.strategy == "authored" or request.template == "authored":
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
        "template-v2": {
            "generation_mode": "standard",
            "mode": "template",
            "version": "v2-standard",
            "theme": {"mode": "template"},
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
    if driver_id == "authored" and not disable_thinking():
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
        persisted = await sql_session.get(PresentationModel, presentation_id)
        try:
            identity = server_generation_profile_identity(request, persisted)
            profile = resolve_presentation_profile(identity)
            required = generation_provider_capabilities(request, profile.driver_id)
        except PresentationProfileError as error:
            raise HTTPException(status_code=422, detail=error.code) from error
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
