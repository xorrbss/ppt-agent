import asyncio
import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

import utils.authored_ocr_quality as ocr_mod
from services import authored_illustration_reviews as reviews
from services.authored_illustration_reviews import (
    ILLUSTRATION_REVIEW_ROOT_NAME,
    ILLUSTRATION_REVIEW_SCHEMA_VERSION,
    IllustrationReviewStore,
    illustration_review_root,
    review_illustration,
)
from services.authored_local_fit_image_text import src_digest


def _run(coro):
    return asyncio.run(coro)


def _png_data_uri(color=(200, 40, 40, 255), size=(8, 8)) -> str:
    image = Image.new("RGBA", size, color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _fake_ocr(monkeypatch, text=""):
    async def fake(_payload, **_kwargs):
        return SimpleNamespace(available=True, status="success", text=text)

    monkeypatch.setattr(ocr_mod, "inspect_authored_ocr", fake)


def _entry(passed=True, **overrides):
    entry = {
        "labels_requested": False,
        "text": {"passed": passed, "method": "ocr", "detail": "no_text_found"},
        "provider": "test",
        "prompt_digest": "sha256:abc",
        "mime": "image/png",
        "created_at": 1.0,
        "schema_version": ILLUSTRATION_REVIEW_SCHEMA_VERSION,
    }
    entry.update(overrides)
    return entry


# -- store -------------------------------------------------------------------


def test_root_and_index_path(tmp_path):
    store = IllustrationReviewStore(app_data=str(tmp_path))
    assert illustration_review_root(str(tmp_path)) == tmp_path / ILLUSTRATION_REVIEW_ROOT_NAME
    assert store.index_path == tmp_path / ILLUSTRATION_REVIEW_ROOT_NAME / "index.json"
    assert store.load() == {}


def test_store_round_trip(tmp_path):
    store = IllustrationReviewStore(app_data=str(tmp_path))
    merged = store.append({"sha256:a": _entry()})
    assert merged == {"sha256:a": _entry()}
    assert IllustrationReviewStore(app_data=str(tmp_path)).load() == {"sha256:a": _entry()}
    document = json.loads(store.index_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == ILLUSTRATION_REVIEW_SCHEMA_VERSION
    assert isinstance(document["updated_at"], float)
    assert set(document["reviews"]) == {"sha256:a"}


def test_append_keeps_first_writer_and_merges_new_keys(tmp_path):
    store = IllustrationReviewStore(app_data=str(tmp_path))
    store.append({"sha256:a": _entry(provider="first")})
    merged = store.append({"sha256:a": _entry(provider="second"), "sha256:b": _entry()})
    assert merged["sha256:a"]["provider"] == "first"
    assert set(merged) == {"sha256:a", "sha256:b"}
    assert set(store.load()) == {"sha256:a", "sha256:b"}
    assert not list(store.root.glob("*.tmp"))


def test_unreadable_index_is_empty(tmp_path):
    store = IllustrationReviewStore(app_data=str(tmp_path))
    store.root.mkdir(parents=True)
    store.index_path.write_text("{not json", encoding="utf-8")
    assert store.load() == {}
    store.index_path.write_text(json.dumps({"reviews": "nope"}), encoding="utf-8")
    assert store.load() == {}


def test_unset_app_data(monkeypatch):
    monkeypatch.setattr(reviews, "get_app_data_directory_env", lambda: None)
    with pytest.raises(RuntimeError):
        illustration_review_root()
    _fake_ocr(monkeypatch)
    result = _run(
        review_illustration(
            _png_data_uri(), labels_requested=False, forbidden=(), provider=None, prompt="p"
        )
    )
    assert result is None


# -- review_illustration -----------------------------------------------------


def test_review_clean_png_records_ocr_pass(tmp_path, monkeypatch):
    _fake_ocr(monkeypatch, text="")
    uri = _png_data_uri()
    entry = _run(
        review_illustration(
            uri,
            labels_requested=False,
            forbidden=("gateway",),
            provider="openai",
            prompt="a gateway",
            app_data=str(tmp_path),
        )
    )
    assert entry is not None
    assert entry["text"]["passed"] is True
    assert entry["text"]["method"] == "ocr"
    assert entry["labels_requested"] is False
    assert entry["provider"] == "openai"
    assert entry["mime"] == "image/png"
    assert entry["prompt_digest"].startswith("sha256:")
    assert entry["schema_version"] == ILLUSTRATION_REVIEW_SCHEMA_VERSION
    stored = IllustrationReviewStore(app_data=str(tmp_path)).load()
    assert stored[src_digest(uri)] == entry


def test_review_dirty_png_records_failure(tmp_path, monkeypatch):
    _fake_ocr(monkeypatch, text="Gateway")
    entry = _run(
        review_illustration(
            _png_data_uri(),
            labels_requested=False,
            forbidden=("gateway",),
            provider="openai",
            prompt="a gateway",
            app_data=str(tmp_path),
        )
    )
    assert entry["text"]["passed"] is False
    assert entry["text"]["method"] == "ocr"
    assert entry["text"]["matched_term"] == "gateway"


def test_review_ocr_unavailable_records_heuristic(tmp_path, monkeypatch):
    async def boom(_payload, **_kwargs):
        raise RuntimeError("no tesseract")

    monkeypatch.setattr(ocr_mod, "inspect_authored_ocr", boom)
    entry = _run(
        review_illustration(
            _png_data_uri(),
            labels_requested=False,
            forbidden=(),
            provider=None,
            prompt="",
            app_data=str(tmp_path),
        )
    )
    assert entry["text"]["passed"] is True
    assert entry["text"]["method"] == "heuristic"


@pytest.mark.parametrize(
    "uri",
    ["", "https://example.com/a.png", "data:image/png,rawbytes", "data:image/png;base64,!!!"],
)
def test_review_malformed_data_uri_returns_none(tmp_path, monkeypatch, uri):
    _fake_ocr(monkeypatch)
    result = _run(
        review_illustration(
            uri, labels_requested=False, forbidden=(), provider=None, prompt="", app_data=str(tmp_path)
        )
    )
    assert result is None
    assert IllustrationReviewStore(app_data=str(tmp_path)).load() == {}


def test_review_labels_requested_recorded_verbatim(tmp_path, monkeypatch):
    _fake_ocr(monkeypatch)
    entry = _run(
        review_illustration(
            _png_data_uri(),
            labels_requested=True,
            forbidden=(),
            provider="gemini",
            prompt="labelled chart",
            app_data=str(tmp_path),
        )
    )
    assert entry["labels_requested"] is True


def test_review_disk_failure_returns_none(tmp_path, monkeypatch):
    _fake_ocr(monkeypatch)

    def broken(_self, _entries):
        raise OSError("disk full")

    monkeypatch.setattr(IllustrationReviewStore, "append", broken)
    result = _run(
        review_illustration(
            _png_data_uri(),
            labels_requested=False,
            forbidden=(),
            provider=None,
            prompt="",
            app_data=str(tmp_path),
        )
    )
    assert result is None


# -- record_generated_illustration: the single write point ----------------------


def _prompt(*, labels: bool) -> str:
    from utils.authored_illustration_prompt import NO_TEXT_SUFFIX

    return "a lighthouse on a cliff. labels allowed" if labels else f"a lighthouse. {NO_TEXT_SUFFIX}"


def test_record_reads_the_label_decision_off_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    _fake_ocr(monkeypatch)
    uri = _png_data_uri()

    _run(reviews.record_generated_illustration(
        uri, prompt=_prompt(labels=False), content="등대 절벽", image_service=SimpleNamespace(),
    ))

    entry = IllustrationReviewStore(app_data=str(tmp_path)).load()[src_digest(uri)]
    assert entry["labels_requested"] is False
    assert entry["text"]["passed"] is True and entry["text"]["method"] == "ocr"


def test_record_marks_a_label_requesting_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    _fake_ocr(monkeypatch)
    uri = _png_data_uri((10, 200, 10, 255))

    _run(reviews.record_generated_illustration(
        uri, prompt=_prompt(labels=True), content="등대", image_service=SimpleNamespace(),
    ))

    assert IllustrationReviewStore(app_data=str(tmp_path)).load()[src_digest(uri)]["labels_requested"] is True


def test_record_passes_slide_content_words_as_forbidden(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    seen = {}

    async def fake(data_uri, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(reviews, "review_illustration", fake)
    _run(reviews.record_generated_illustration(
        _png_data_uri(), prompt=_prompt(labels=False), content="등대 절벽 a 등대",
        image_service=SimpleNamespace(),
    ))

    assert seen["forbidden"] == ["등대", "절벽"]
    assert seen["labels_requested"] is False


def test_record_skips_a_missing_picture_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    _run(reviews.record_generated_illustration(
        None, prompt=_prompt(labels=False), content="x", image_service=SimpleNamespace(),
    ))
    assert not (tmp_path / "authored-illustration-reviews").exists()

    async def boom(*_args, **_kwargs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(reviews, "review_illustration", boom)
    _run(reviews.record_generated_illustration(  # must not raise
        _png_data_uri(), prompt=_prompt(labels=False), content="x", image_service=SimpleNamespace(),
    ))
