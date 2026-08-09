from __future__ import annotations

import inspect

from services import slidespec_authored_executor as executor


def test_repair_context_facade_keeps_the_public_call_shape() -> None:
    parameters = inspect.signature(executor._repair_context).parameters

    assert tuple(parameters) == (
        "request",
        "evidence",
        "browser_metrics",
        "constraint_report",
    )
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parameters.values())
    assert parameters["request"].default is inspect.Parameter.empty
    assert parameters["evidence"].default is inspect.Parameter.empty
    assert parameters["browser_metrics"].default is None
    assert parameters["constraint_report"].default is None


def test_repair_context_facade_forwards_runtime_type_and_renderer(
    monkeypatch,
) -> None:
    class FakeRequest:
        pass

    request = FakeRequest()
    evidence = object()
    result = {"preserved": True}
    observed: dict[str, object] = {}

    def runtime_renderer(value):
        return value

    def fake_builder(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return result

    monkeypatch.setattr(executor, "RenderRequest", FakeRequest)
    monkeypatch.setattr(executor, "render_slidespec", runtime_renderer)
    monkeypatch.setattr(executor, "build_repair_context", fake_builder)

    assert executor._repair_context(request, evidence) is result
    assert observed == {
        "args": (request, evidence, None, None),
        "kwargs": {
            "render_request_type": FakeRequest,
            "render_native": runtime_renderer,
        },
    }
