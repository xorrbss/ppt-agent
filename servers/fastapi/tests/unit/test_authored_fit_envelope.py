"""The scene envelope follows the fitted document (bench run 43 regression)."""

from __future__ import annotations

import uuid

import pytest

from services import authored_fit_envelope
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_fit_envelope import reseal_scene_envelope_after_fit
from services.authored_vnext.generation.scene_envelope import seal_slide_scene_envelope
from utils.authored_dom_quality import ensure_authored_qids
from utils.authored_semantic_role_repair import canonicalize_authored_roles

_DRAFT = canonicalize_authored_roles(ensure_authored_qids(
    "<!doctype html><html><head><meta charset='utf-8'></head>"
    "<body><main><h1>Fit</h1><p>copy</p></main></body></html>"
))
_FITTED = _DRAFT.replace("<p ", '<p style="font-size:15px" ', 1)


def test_no_edits_keeps_the_sealed_envelope():
    sealed = seal_slide_scene_envelope(_DRAFT, slide_index=0)
    assert reseal_scene_envelope_after_fit(
        fitted_html=None, scene_envelope=sealed, slide_index=0, generation_job_id=None
    ) is sealed
    assert reseal_scene_envelope_after_fit(
        fitted_html=_DRAFT, scene_envelope=sealed, slide_index=0, generation_job_id=None
    ) is sealed


def test_a_fitted_document_reseals_and_persists_for_a_durable_job(monkeypatch):
    persisted: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        authored_fit_envelope, "persist_scene_envelope",
        lambda job_id, envelope: persisted.append((job_id, envelope.html_projection)),
    )
    sealed = seal_slide_scene_envelope(_DRAFT, slide_index=0)
    job_id = uuid.uuid4()
    resealed = reseal_scene_envelope_after_fit(
        fitted_html=_FITTED, scene_envelope=sealed, slide_index=0, generation_job_id=job_id
    )
    assert resealed.html_projection == _FITTED
    assert resealed.scene_digest != sealed.scene_digest
    assert persisted == [(job_id, _FITTED)]


def test_an_unsealable_fitted_document_is_a_render_stage_error():
    sealed = seal_slide_scene_envelope(_DRAFT, slide_index=0)
    with pytest.raises(AuthoredStageError) as raised:
        reseal_scene_envelope_after_fit(
            fitted_html="<main>no qids</main>", scene_envelope=sealed,
            slide_index=0, generation_job_id=None,
        )
    assert raised.value.code == "authored_scene_envelope_invalid"
    assert raised.value.stage == "render"
