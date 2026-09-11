"""Image-text evidence restated from a real on-disk icon pack.

Every case builds a pack under ``tmp_path`` with ``IconPack.write_icon`` and
``append`` so the lookup runs against the same index the generator writes.
"""

from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from services.authored_icon_pack.inline import icon_data_uri
from services.authored_icon_pack.store import IconEntry, IconPack, concept_key
from services.authored_local_fit_policy import image_text_evidence_for_html

PACK_KEY = "korean-biz/default"


def _png(color: tuple[int, int, int, int]) -> bytes:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((8, 8, 56, 56), fill=color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _text_check(*, passed: bool, method: str) -> dict:
    return {"passed": passed, "method": method, "detail": "", "matched_term": None,
            "longest_latin_run": 0}


def _add_icon(pack: IconPack, concept: str, color, *, passed=True, method="ocr") -> IconEntry:
    key = concept_key(concept)
    name = pack.write_icon(key, _png(color))
    entry = IconEntry(
        concept=concept, file=name, width=64, height=64, sheet=None, cell=0,
        checks={"transparency": {"passed": True}, "text": _text_check(passed=passed, method=method)},
        created_at=time.time(),
    )
    pack.append({key: entry})
    return entry


@pytest.fixture
def app_data(tmp_path: Path, monkeypatch) -> str:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def pack(app_data: str) -> IconPack:
    return IconPack(PACK_KEY, app_data=app_data)


def _uri(pack: IconPack, entry: IconEntry) -> str:
    uri = icon_data_uri(pack.path_for(entry))
    assert uri
    return uri


def test_ocr_passed_icon_gets_record_with_ancestor_qid(app_data, pack):
    entry = _add_icon(pack, "sensing", (30, 94, 255, 255))
    uri = _uri(pack, entry)
    html = f'<section><div data-qid="s1.n1.ic"><img src="{uri}" alt=""></div></section>'

    records = image_text_evidence_for_html(html, app_data=app_data)

    assert len(records) == 1
    record = records[0]
    assert record["qid"] == "s1.n1.ic"
    assert record["text_free"] is True
    assert record["provenance"] == "icon_pack"
    assert record["asset_digest"] == "sha256:" + hashlib.sha256(uri.encode("utf-8")).hexdigest()
    file_hash = hashlib.sha256(pack.path_for(entry).read_bytes()).hexdigest()
    assert record["review_digest"] == f"icon-pack:{PACK_KEY}:{entry.file}:{file_hash}"
    assert record["review_digest"].startswith("icon-pack:")


def test_image_own_qid_wins_over_ancestor(app_data, pack):
    entry = _add_icon(pack, "forecast", (200, 40, 40, 255))
    uri = _uri(pack, entry)
    html = f'<div data-qid="parent"><img data-qid="own" src="{uri}"></div>'

    records = image_text_evidence_for_html(html, app_data=app_data)

    assert [record["qid"] for record in records] == ["own"]


def test_heuristic_pass_is_not_verified(app_data, pack):
    entry = _add_icon(pack, "alert", (0, 160, 90, 255), passed=True, method="heuristic")
    html = f'<div data-qid="q"><img src="{_uri(pack, entry)}"></div>'

    assert image_text_evidence_for_html(html, app_data=app_data) == []


def test_failed_text_check_gets_no_record(app_data, pack):
    entry = _add_icon(pack, "label", (90, 90, 90, 255), passed=False, method="ocr")
    html = f'<div data-qid="q"><img src="{_uri(pack, entry)}"></div>'

    assert image_text_evidence_for_html(html, app_data=app_data) == []


def test_unknown_data_uri_and_non_data_src_get_no_record(app_data, pack):
    _add_icon(pack, "known", (30, 94, 255, 255))
    foreign = "data:image/png;base64," + "AAAA"
    html = (
        f'<div data-qid="a"><img src="{foreign}"></div>'
        '<div data-qid="b"><img src="/app_data/images/photo.png"></div>'
        '<div data-qid="c"><img src="https://example.com/x.png"></div>'
    )

    assert image_text_evidence_for_html(html, app_data=app_data) == []


def test_image_without_qid_ancestor_gets_no_record(app_data, pack):
    entry = _add_icon(pack, "orphan", (30, 94, 255, 255))
    uri = _uri(pack, entry)
    html = f'<section><div class="wrap"><img src="{uri}"></div></section>'

    assert image_text_evidence_for_html(html, app_data=app_data) == []


def test_sibling_scope_does_not_leak_qid(app_data, pack):
    entry = _add_icon(pack, "sibling", (30, 94, 255, 255))
    uri = _uri(pack, entry)
    html = f'<div data-qid="first"></div><div><img src="{uri}"></div>'

    assert image_text_evidence_for_html(html, app_data=app_data) == []


def test_malformed_html_returns_empty(app_data, pack):
    _add_icon(pack, "broken", (30, 94, 255, 255))
    html = '<div data-qid="q"><img src="data:image/png;base64,QUJD" <<<< </div></p><'

    assert image_text_evidence_for_html(html, app_data=app_data) == []
    assert image_text_evidence_for_html("", app_data=app_data) == []


def test_unset_or_missing_app_data_root_returns_empty(tmp_path, monkeypatch):
    html = '<div data-qid="q"><img src="data:image/png;base64,QUJD"></div>'
    monkeypatch.delenv("APP_DATA_DIRECTORY", raising=False)
    assert image_text_evidence_for_html(html) == []
    assert image_text_evidence_for_html(html, app_data=str(tmp_path / "absent")) == []
