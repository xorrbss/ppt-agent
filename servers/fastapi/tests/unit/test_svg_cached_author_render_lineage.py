"""The retired cached-author-render recovery still recognises and rejects its stored records."""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import svg_cached_author_render_lineage as cached
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg import collect
from services.svg_checkpoint_recovery import (
    SVG_CACHED_AUTHOR_RENDER, SVG_CACHED_AUTHOR_RENDER_V2, SVG_CACHED_AUTHOR_RENDER_V3,
)
from services.svg_recovery_witness import digest, unknown_provider
from utils.hashing import canonical_json_sha256


def test_stored_revisions_are_still_recognised():
    assert cached.REVISIONS == {SVG_CACHED_AUTHOR_RENDER, SVG_CACHED_AUTHOR_RENDER_V2, SVG_CACHED_AUTHOR_RENDER_V3}
    assert [cached.marker_schema(revision) for revision in sorted(cached.REVISIONS)] == [
        cached.MARKER_SCHEMA, cached.MARKER_SCHEMA_V2, cached.MARKER_SCHEMA_V3]


def test_shared_witnesses_keep_their_bytes():
    value = {"b": [1.5, float("nan")], "a": "한"}
    assert digest(value) == canonical_json_sha256(value, allow_nan=True)
    assert unknown_provider({"x": [{"y": "provider_outcome:public_provider_result_unknown"}]})
    assert not unknown_provider({"x": ["public_provider_overload"], "y": 3})


def test_a_marker_never_approves_sidecars():
    assert cached.approved_sidecars({"svg_content": "<svg/>"}) == {}
    assert cached.approved_sidecars({"cached_author_render": {}}) == {}
    with pytest.raises(cached.CachedAuthorRenderRejected, match="svg_cached_author_render_sidecar_untrusted"):
        cached.approved_sidecars({"cached_author_render": {"revision": cached.REVISION_V3, "slide_index": 0}})


def test_a_stored_marker_page_is_rejected_before_any_render():
    with pytest.raises(AuthoredStageError) as error:
        collect._cached_author_render_page(2)
    assert (error.value.code, error.value.slide_index, error.value.stage) == (
        "svg_cached_author_render_invalid", 2, "render")


def _operation(**overrides):
    return SimpleNamespace(**{"id": uuid.uuid4(), "version": 3, "retry_of_operation_id": uuid.uuid4(),
                              "owner_scope": "scope", "owner_id": uuid.uuid4(), "presentation_id": uuid.uuid4(),
                              **overrides})


def _verify(revision, rows, *, operation=None, **request_overrides):
    operation = operation or _operation()
    key = cached.action_id_for(operation, 7, revision=revision)
    request = SimpleNamespace(**{"recovery_revision": revision, "action": "retry", "expected_operation_version": 3,
                                 "expected_revision": 7, "action_id": key, "idempotency_key": key,
                                 **request_overrides})
    session = SimpleNamespace(scalar=AsyncMock(side_effect=list(rows)))
    with pytest.raises(cached.CachedAuthorRenderRejected) as error:
        asyncio.run(cached.verify_action(session, operation, request, presentation_revision=7))
    return error.value.code, session.scalar.await_count


@pytest.mark.parametrize("revision", sorted(cached.REVISIONS))
def test_an_approval_request_without_its_action_key_is_rejected(revision):
    assert _verify(revision, [], action="edit") == ("svg_cached_author_render_approval_required", 0)
    assert _verify(revision, [], action_id="other") == ("svg_cached_author_render_approval_required", 0)
    assert _verify(revision, [], expected_revision=None) == ("svg_cached_author_render_approval_required", 0)


def test_every_approval_request_fails_on_the_unapproved_converter():
    found = SimpleNamespace(revision=1)
    child = _operation()
    assert _verify(cached.REVISION, []) == ("svg_cached_author_render_converter_unapproved", 0)
    assert _verify(cached.REVISION_V2, [child, found]) == ("svg_cached_author_render_converter_unapproved", 2)
    assert _verify(cached.REVISION_V3, [child, found, child, found]) == (
        "svg_cached_author_render_converter_unapproved", 4)


def test_v2_and_v3_requests_still_prove_their_prior_children_first():
    unproven = "svg_cached_author_render_prior_child_unproven"
    found = SimpleNamespace(revision=1)
    assert _verify(cached.REVISION_V2, [], operation=_operation(retry_of_operation_id=None)) == (unproven, 0)
    assert _verify(cached.REVISION_V2, [None]) == (unproven, 1)
    assert _verify(cached.REVISION_V2, [_operation(), None]) == (unproven, 2)
    assert _verify(cached.REVISION_V3, [_operation(retry_of_operation_id=None)]) == (unproven, 1)
    assert _verify(cached.REVISION_V3, [_operation(), None]) == (unproven, 2)
    assert _verify(cached.REVISION_V3, [_operation(), found, None]) == (unproven, 3)
    assert _verify(cached.REVISION_V3, [_operation(), found, _operation(), None]) == (unproven, 4)
