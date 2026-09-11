"""End to end through real Chrome: a slide whose only raster is an OCR-verified
icon-pack icon is fitted; the same slide with an unverified icon is not.

The producer (``image_text_evidence_for_html``) restates the pack index, the
transport (``capture_with_policy``) puts its records on the policy and copies the
runner's verdicts onto the element metrics, and the runner keeps the only
judgment. Nothing here monkeypatches the runner or the validator: if either
half of the bridge is missing, the verified case fails exactly as production
did before the bridge existed.
"""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path

import pytest
from PIL import Image

from services import authored_local_fit_image_text as producer_mod
from services.authored_icon_pack.inline import icon_data_uri
from services.authored_icon_pack.sheet import encode_png
from services.authored_icon_pack.store import IconEntry, IconPack, concept_key
from services.authored_local_fit_policy import html_digest, local_fit_policy
from utils.slide_capture import find_chrome, render_html_to_png_with_metrics

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")

_ICON_QID = "s1.n1.ic"


def _icon_png(color: tuple[int, int, int, int] = (30, 90, 200, 255)) -> bytes:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for x in range(16, 48):
        for y in range(16, 48):
            image.putpixel((x, y), color)
    return encode_png(image)


def _pack_with_icon(tmp_path: Path, *, method: str) -> str:
    """A temp icon pack holding one icon whose text check used ``method``."""
    pack = IconPack("test/pack", app_data=str(tmp_path))
    key = concept_key("sensor")
    file_name = pack.write_icon(key, _icon_png())
    pack.append({
        key: IconEntry(
            concept="sensor", file=file_name, width=64, height=64,
            checks={"text": {"passed": True, "method": method, "detail": "test"},
                    "transparency": {"passed": True}},
            created_at=time.time(),
        ),
    })
    uri = icon_data_uri(pack.path_for(pack.load()[key]))
    assert uri, "the pack icon did not inline"
    return uri


def _slide(icon_uri: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "html,body{margin:0;width:1280px;height:720px;overflow:hidden;font-family:sans-serif}"
        ".card{position:absolute;left:80px;top:80px;width:560px;height:200px;padding:16px;"
        "border:1px solid #888;display:flex;gap:16px;align-items:flex-start}"
        ".ig-ic{width:64px;height:64px;flex:0 0 64px}.ig-ic img{width:64px;height:64px}"
        ".body{font-size:18px;line-height:1.4}"
        "</style></head><body>"
        '<section class="card" data-qid="s1.card">'
        f'<div class="ig-ic" data-qid="{_ICON_QID}"><img src="{icon_uri}" alt=""></div>'
        '<p class="body" data-qid="s1.body">센서 배치 계획: 주 모터에는 진동 센서, '
        "크랭크 베어링에는 온도 센서, 유압 유닛에는 압력 센서를 둔다.</p>"
        "</section></body></html>"
    )


def _fit(html: str):
    digest = html_digest(html)
    policy = local_fit_policy(author_revision=digest, original_html_digest=digest, slide_index=0)
    return asyncio.run(render_html_to_png_with_metrics(
        html, timeout=60, fit=True, fit_policy=policy, fresh=True,
    ))


def _element(capture, qid: str) -> dict:
    for item in (capture.browser_metrics or {}).get("elements", []):
        if item.get("qid") == qid:
            return item
    raise AssertionError(f"no element metrics for {qid}")


def test_an_ocr_verified_pack_icon_lets_the_fit_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(producer_mod, "icon_pack_root",
                        lambda app_data=None: tmp_path / "authored-icon-packs")
    uri = _pack_with_icon(tmp_path, method="ocr")

    capture = _fit(_slide(uri))
    report = capture.fit_report

    assert "font_unverifiable" not in report["unresolved_reasons"]
    assert report["status"] != "unresolved" or report["unresolved_reasons"] == ["candidates_exhausted"]
    records = report["image_text"]
    assert [item["qid"] for item in records] == [_ICON_QID]
    assert records[0]["verified"] is True and records[0]["scope"] == "verified_no_text"
    # The transport copied the runner's verdict onto the element the validator reads.
    evidence = _element(capture, _ICON_QID).get("image_text_evidence")
    assert evidence == {"status": "no_text", "scope": "verified_no_text",
                        "asset_identity": records[0]["asset_identity"]}


def test_a_heuristic_only_icon_still_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(producer_mod, "icon_pack_root",
                        lambda app_data=None: tmp_path / "authored-icon-packs")
    uri = _pack_with_icon(tmp_path, method="heuristic")

    capture = _fit(_slide(uri))
    report = capture.fit_report

    assert "font_unverifiable" in report["unresolved_reasons"]
    assert report["status"] == "unresolved"
    assert report["image_text"][0]["scope"] == "unverifiable"
    assert "image_text_evidence" not in _element(capture, _ICON_QID)


def test_an_icon_the_pack_never_recorded_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(producer_mod, "icon_pack_root",
                        lambda app_data=None: tmp_path / "authored-icon-packs")
    _pack_with_icon(tmp_path, method="ocr")
    # A different picture, so its data URI cannot coincide with the pack icon's.
    stranger = "data:image/png;base64," + base64.b64encode(
        _icon_png((200, 40, 40, 255))
    ).decode("ascii")

    capture = _fit(_slide(stranger))
    report = capture.fit_report

    assert "font_unverifiable" in report["unresolved_reasons"]
    assert "image_text_evidence" not in _element(capture, _ICON_QID)
