"""Pin dependency direction and the service-owned stream error policy."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import ppt_writing_policy, svg_contract, svg_reference_identity
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg.partial_quality_recovery import is_current_provider_control_error
from services.provider_runtime_primitives import RuntimeControlError
from services.public_operation_admission_models import PublicAdmissionRejected
from utils import codex_stream_adapter as adapter
from utils import ppt_writing_prompt, svg_color_identity, svg_validation
from utils.provider_overload import ProviderOutcomeError


ROOT = Path(__file__).resolve().parents[2]
UTILITY_PATHS = (
    "authored_style_svg_recovery.py", "codex_stream_adapter.py",
    "llm_calls/generate_content_brief.py", "llm_calls/generate_presentation_outlines.py",
    "llm_calls/prepare_svg_common_header_titles.py", "svg_template_rules.py",
    "ppt_writing_prompt.py", "svg_color_identity.py", "svg_validation.py",
)


@pytest.mark.parametrize("path", UTILITY_PATHS)
def test_utilities_have_no_service_dependency_even_inside_functions(path):
    tree = ast.parse((ROOT / "utils" / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "services"
        elif isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "services" for alias in node.names)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name in {"__import__", "import_module"}:
                assert not any(
                    isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    and arg.value.startswith("services") for arg in node.args
                )


def test_service_exports_share_the_only_pure_implementation():
    assert svg_contract.validate_svg is svg_validation.validate_svg
    assert svg_contract.source_digest is svg_validation.source_digest
    assert svg_contract.normalize_svg_response is svg_validation.normalize_svg_response
    assert ppt_writing_policy.writing_policy_prompt is ppt_writing_prompt.writing_policy_prompt
    assert ppt_writing_policy.WRITING_POLICY_ID == ppt_writing_prompt.WRITING_POLICY_ID
    assert svg_reference_identity.contrast_foreground is svg_color_identity.contrast_foreground
    assert svg_reference_identity.identity_border is svg_color_identity.identity_border
    assert not (ROOT / "utils/llm_calls/svg_common_header_design.py").exists()


def test_observer_policy_is_required_scoped_and_retained_by_transport_clone():
    with pytest.raises(TypeError):
        with adapter.observe_codex_provider_events(lambda event: None):
            pass
    events = []
    transport = SimpleNamespace(responses=object())
    transport.with_options = lambda **kwargs: transport
    before = adapter._PROVIDER_EVENT_OBSERVER.get()
    with adapter.observe_codex_provider_events(events.append, is_control_error=is_current_provider_control_error):
        observer = adapter._PROVIDER_EVENT_OBSERVER.get()
        client = adapter._ObservedClient(transport, observer=observer).with_options(max_retries=0)
    assert adapter._PROVIDER_EVENT_OBSERVER.get() is before
    assert client._observer is observer
    assert client.responses._observer is observer
    observer({"terminal_event": "dispatch_ready"})
    assert events == [{"terminal_event": "dispatch_ready"}]
    assert observer.is_control_error is is_current_provider_control_error


def _control_errors():
    return [
        RuntimeControlError("cancelled"),
        PublicAdmissionRejected("public_provider_request_limit_exceeded"),
        AuthoredStageError(slide_index=0, stage="author", code="authored_attempt_lost"),
    ]


@pytest.mark.parametrize("error", _control_errors())
def test_observer_control_errors_keep_identity_after_stream_completion(error):
    def observe(event):
        raise error

    stream = iter([{"type": "response.completed", "response": {"output_text": "<svg/>"}}])
    with adapter.observe_codex_provider_events(observe, is_control_error=is_current_provider_control_error):
        observer = adapter._PROVIDER_EVENT_OBSERVER.get()
    with pytest.raises(type(error)) as caught:
        list(adapter._ObservedResponses._events(stream, observer=observer))
    assert caught.value is error


@pytest.mark.parametrize("error", _control_errors())
def test_llmai_wrapping_restores_the_same_control_error(monkeypatch, error):
    def wrapped_stream(self, **kwargs):
        raise RuntimeError("SDK wrapper") from error
        yield

    monkeypatch.setattr(adapter.ChatGPTClient, "_generate_responses_stream", wrapped_stream)
    with adapter.observe_codex_provider_events(lambda event: None, is_control_error=is_current_provider_control_error):
        observer = adapter._PROVIDER_EVENT_OBSERVER.get()
    client = object.__new__(adapter.RepositoryChatGPTClient)
    client._client = SimpleNamespace(_observer=observer)
    with pytest.raises(type(error)) as caught:
        list(client._generate_responses_stream())
    assert caught.value is error


def test_non_control_observer_failure_remains_a_typed_postresponse_outcome():
    def observe(event):
        raise ValueError("checkpoint write failed")

    with adapter.observe_codex_provider_events(observe, is_control_error=is_current_provider_control_error):
        observer = adapter._PROVIDER_EVENT_OBSERVER.get()
    stream = iter([{"type": "response.completed", "response": {"output_text": "<svg/>"}}])
    with pytest.raises(ProviderOutcomeError) as caught:
        list(adapter._ObservedResponses._events(stream, observer=observer))
    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.parametrize("disabled,owner,tenant", [(True, "owner", "tenant"), (False, None, "tenant"),
                                                 (False, "owner", None), (False, "owner", "tenant")])
def test_recovery_evidence_rejects_untrusted_context_before_database_access(monkeypatch, disabled, owner, tenant):
    from services.svg_recovery_source_evidence import verified_recovery_source_evidence
    from utils import get_env, owner_context

    monkeypatch.setattr(get_env, "is_disable_auth_enabled", lambda: disabled)
    monkeypatch.setattr(owner_context, "get_current_owner_id", lambda: owner)
    monkeypatch.setattr(owner_context, "get_current_tenant_id", lambda: tenant)
    # An untrusted context or missing private provenance cannot reach a session.
    assert asyncio.run(verified_recovery_source_evidence([{}], object())) == []
