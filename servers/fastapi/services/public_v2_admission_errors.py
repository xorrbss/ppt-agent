"""Deterministic-vs-unavailable error classification for public v2 admission.

Split out of ``public_v2_orchestrator.py`` (file-size ratchet) rather than
inlined there: both helpers translate an exception raised by a lower layer
into either a client-facing 422 admission code or a genuine 503, and neither
touches orchestrator state.
"""

from __future__ import annotations

from pydantic import ValidationError


def generation_request_admission_error_code(error: ValidationError) -> str:
    """Recover a stable admission code from a bridge-model validation error.

    ``GeneratePresentationRequest``'s ``model_validator``s raise a bare
    ``ValueError`` with the intended reason code as its message (e.g.
    ``source_content_template_required``); pydantic wraps that into a
    ``value_error`` entry whose ``msg`` is prefixed with ``"Value error, "``.
    """

    first = error.errors()[0]
    if first["type"] == "value_error":
        return str(first["msg"]).removeprefix("Value error, ")
    return "source_content_request_invalid"


def generation_route_error_types() -> tuple[type[BaseException], ...]:
    """Deterministic route-resolution rejections, distinct from real outages.

    ``GenerationRouteError`` (raised for e.g. an unsupported pack+restyle
    combination) is a client-facing admission rejection, not a resolver
    failure -- unlike an unexpected exception from the resolver itself.
    """

    try:
        from services.authored_vnext.generation_route import GenerationRouteError
    except ModuleNotFoundError as error:
        if error.name == "services.authored_vnext.generation_route":
            return ()
        raise
    return (GenerationRouteError,)


__all__ = [
    "generation_request_admission_error_code",
    "generation_route_error_types",
]
