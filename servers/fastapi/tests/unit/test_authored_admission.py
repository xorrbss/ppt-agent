from __future__ import annotations

from functools import wraps

import asyncio
import json
from pathlib import Path

import pytest

from services.authored_rollout_profile import RolloutProfile, rollout_profile_scope


def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


MANIFEST = {
    "units": [
        {
            "sourceUnitId": "u1",
            "sourceIndex": 0,
            "blocks": [
                {"sourceBlockId": "b1", "kind": "text", "order": 0, "payload": {"text": "원문 제목"}},
            ],
        }
    ]
}
OUTLINE = {"slides": [{"sourceUnitId": "u1", "sourceIndex": 0, "readingOrder": ["b1"]}]}
HTML = "<!doctype html><html><head><style>body{margin:0}</style></head><body><h1>원문 제목</h1></body></html>"


KNOWN_DEAD_READS = {  # Dead reads do not grant renderer permission.
    "services/source_content/browser_readback.py": {"data-source-image", "data-decorative", "data-decoration"},
    "utils/slide_capture_fit_observe.py": {"data-role"},
    "utils/slide_capture_fit_geometry.py": {"data-role"},
}


# --- N1/N2: the session-less writer admission -------------------------------

#: A 1x1 PNG, the smallest asset a renderer-produced CSS ``url(data:)`` can
#: carry.  The static-output policy re-encodes it; the authored policy refuses
#: the declaration outright, which is exactly the disagreement under test.
ONE_PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
#: Shaped like renderer output, not like authored HTML: the authored policy
#: rejects it and the static-output policy admits it.
RENDERER_PROFILE_HTML = (
    "<!doctype html><html><head><style>body{margin:0}"
    f".scene{{background:url(data:image/png;base64,{ONE_PIXEL_PNG})}}"
    '</style></head><body><div class="scene">장면</div></body></html>'
)
#: Refused by every profile, so the gate must still refuse it.
UNRENDERABLE_HTML = (
    "<!doctype html><html><head><style>body{margin:0}</style></head>"
    "<body><iframe></iframe></body></html>"
)


def vnext_preview_html(root):
    """A real ``build_preview`` document, reached through the master service."""

    from models.authored_vnext.master.contract import DeckMasterContract
    from models.authored_vnext.master.operations import (
        CreateDraftRequest,
        MasterCasRequest,
        PreviewRequest,
    )
    from services.authored_vnext.master.file_repository import (
        FileAuthoredVNextMasterRepository,
    )
    from services.authored_vnext.master.service import AuthoredVNextMasterService
    from tests.support.authored_vnext_master_fixture import (
        long_title_slide_payload,
        master_contract_payload,
    )

    service = AuthoredVNextMasterService(
        FileAuthoredVNextMasterRepository(root / "authored-vnext-store")
    )
    draft = service.create_draft(
        CreateDraftRequest(
            contract=DeckMasterContract.model_validate(master_contract_payload())
        )
    )
    published = service.publish(
        draft.master_id,
        MasterCasRequest(expected_revision=draft.revision, expected_digest=draft.digest),
    )
    return service.preview(
        PreviewRequest(
            master_id=published.master_id,
            master_revision=published.revision,
            master_digest=published.digest,
            slide=long_title_slide_payload(),
        )
    ).html


def author_checkpoint_write(checkpoint, html, **changes):
    kwargs = dict(
        admission_receipt=None,
        html=html,
        stage="author",
        slide_index=0,
        attempt_token="attempt-1",
        fencing_token=4,
    )
    kwargs.update(changes)
    return checkpoint.admit_checkpoint_write(**kwargs)


def test_capture_and_gate_share_one_profile_selection():
    """The renderer's own admission is that same selection, not a copy of it."""

    from utils import slide_capture
    from utils.renderer_profile import select_render_profile

    captured = slide_capture._admit_render_source(RENDERER_PROFILE_HTML)
    selected = select_render_profile(RENDERER_PROFILE_HTML).require()
    assert captured.source_digest == selected.source_digest
    assert captured.node_count == selected.node_count
    assert captured.asset_digests == selected.asset_digests


#: Shaped like ``_native_scene_element_html``'s image branch: the absolutely
#: positioned, rotated ``<img>`` a retained PPTX scene emits per source shape.
def native_scene_image(index):
    return (
        '<img class="native-scene-element native-scene-image" '
        f'title="native slide 2:{index}" src="data:image/png;base64,{ONE_PIXEL_PNG}" '
        'alt="" style="position:absolute;left:0px;top:0px;width:160px;height:90px;'
        'transform:rotate(0deg);transform-origin:center center;overflow:hidden;'
        'object-fit:fill">'
    )


def native_scene_document(images=200):
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "body{margin:0;width:1280px;height:720px;position:relative}</style></head>"
        "<body>" + "".join(native_scene_image(index) for index in range(images)) + "</body></html>"
    )


# --- R7/B4: one frozen rollout profile per attempt -------------------------


def test_the_single_repair_loop_is_refused_outside_an_enforcing_gate(monkeypatch):
    """One bounded repair is only meaningful when the gate's verdict is acted on.

    Under ``shadow`` the admission gate records a verdict nothing enforces, so a
    single-loop profile spends its one repair budget on advice that was never
    enforced and publishes anyway. The combination is refused where a profile is
    resolved -- from the environment and from a job's durable pin alike.
    """

    from services import authored_rollout_profile as rollout
    from services.authored_rollout_profile import (
        RolloutProfileError,
        rollout_profile_from_payload,
    )

    monkeypatch.setattr(
        rollout, "is_authored_verbatim_single_repair_loop_enabled", lambda: True
    )
    monkeypatch.setattr(rollout, "get_authored_admission_gate_mode", lambda: "shadow")
    with pytest.raises(RolloutProfileError, match="rollout_profile_invalid") as refused:
        rollout.resolve_rollout_profile()
    assert refused.value.code == "authored_rollout_profile_invalid"
    assert refused.value.retryable is False
    assert "gate_mode" in refused.value.detail

    with pytest.raises(RolloutProfileError, match="rollout_profile_invalid"):
        rollout_profile_from_payload(
            RolloutProfile(single_repair_loop=True).as_dict()
        )

    monkeypatch.setattr(rollout, "get_authored_admission_gate_mode", lambda: "enforce")
    enforced = rollout.resolve_rollout_profile()
    assert (enforced.gate_mode, enforced.single_repair_loop) == ("enforce", True)
    assert rollout_profile_from_payload(enforced.as_dict()) == enforced


def test_a_second_disagreeing_pin_is_a_coded_rejection():
    from services.authored_rollout_profile import RolloutProfileError, pin_rollout_profile

    first = RolloutProfile()
    second = RolloutProfile(gate_mode="enforce")
    with rollout_profile_scope(first):
        pin_rollout_profile(first, replacing=first)
        with pytest.raises(RolloutProfileError, match="rollout_profile_mismatch"):
            pin_rollout_profile(second, replacing=first)


AUTHOR_FIXTURES = (
    Path(__file__).resolve().parents[4]
    / "artifacts"
    / "verbatim-design-measurements-20260911"
    / "fixtures"
)


def _author_fixtures():
    """Stored author-stage documents with their one-unit manifest and bindings."""

    if not AUTHOR_FIXTURES.is_dir():
        return []
    cases = []
    for run in sorted(AUTHOR_FIXTURES.iterdir()):
        for slide in sorted(run.iterdir()) if run.is_dir() else ():
            document = slide / "html-author.txt"
            if not document.is_file():
                continue
            manifest = json.loads((slide / "manifest.json").read_text(encoding="utf-8"))
            projection = json.loads((slide / "projection.json").read_text(encoding="utf-8"))
            blocks = {str(block.get("sourceBlockId")): block for block in manifest.get("blocks", ())}
            bindings = tuple(
                (block_id, (blocks[block_id].get("payload") or {})["text"])
                for block_id in projection
                if isinstance((blocks.get(block_id, {}).get("payload") or {}).get("text"), str)
                and (blocks[block_id]["payload"]["text"]).strip()
            )
            if bindings:
                cases.append((f"{run.name}/{slide.name}", document.read_text(encoding="utf-8"), manifest, bindings))
    return cases


