from __future__ import annotations

from copy import deepcopy

import pytest

from templates.v2.publication import evaluate_template_v2_publication


SHA256 = "a" * 64
PREFIXED_SHA256 = f"sha256:{SHA256}"


def _native_clone_assets() -> dict[str, object]:
    return {
        "thumbnail": "https://example.invalid/preview.png",
        "native_pptx": {
            "schema_version": "presenton.template-v2-native-source/v1",
            "preservation_mode": "native_clone",
            "storage_key": "template-v2/native/source.pptx",
            "source_sha256": SHA256,
            "source_size_bytes": 1024,
            "layout_source_parts": {
                "title": "ppt/slides/slide1.xml",
                "content": "ppt/slides/slide2.xml",
            },
            "capabilities": {
                "native_clone": True,
                "text_patch": True,
                "table_patch": True,
                "master_layout_theme": True,
                "relationships_media": True,
            },
            "publication_status": "publishable",
        },
    }


def _reconstructed_assets() -> dict[str, object]:
    return {
        "native_pptx": {
            "schema_version": "presenton.template-v2-publication/v1",
            "preservation_mode": "reconstructed",
            "publication_status": "publishable",
            "publishable": True,
            "native_pptx_preflight": {
                "schema_version": "presenton.template-v2-native-pptx/v1",
                "compiler_name": "presenton-template-v2-native-ooxml",
                "compiler_version": "1",
                "preservation_mode": "reconstructed",
                "status": "compiled",
                "selected_for_export": False,
                "structural_sha256": PREFIXED_SHA256,
                "package_sha256": PREFIXED_SHA256,
                "unsupported": None,
            },
        }
    }


def _set_path(source: dict[str, object], path: tuple[str, ...], value: object) -> None:
    target: dict[str, object] = source
    for part in path[:-1]:
        child = target[part]
        assert isinstance(child, dict)
        target = child
    target[path[-1]] = value


def test_native_clone_publication_requires_complete_versioned_source_evidence():
    assets = _native_clone_assets()
    before = deepcopy(assets)

    decision = evaluate_template_v2_publication(assets)

    assert decision.publishable is True
    assert decision.reason_code == "template_v2_publication_ready"
    assert decision.preservation_mode == "native_clone"
    assert assets == before


def test_native_clone_allows_explicit_publishable_true_for_new_writers():
    assets = _native_clone_assets()
    native = assets["native_pptx"]
    assert isinstance(native, dict)
    native["publishable"] = True

    assert evaluate_template_v2_publication(assets).publishable is True


@pytest.mark.parametrize("assets", [None, {}, [], "publishable"])
def test_publication_fails_closed_when_evidence_is_missing(assets: object):
    decision = evaluate_template_v2_publication(assets)

    assert decision.publishable is False


@pytest.mark.parametrize("status", [None, "blocked", "published", True, 1])
def test_publication_requires_exact_publishable_status(status: object):
    assets = _native_clone_assets()
    _set_path(assets, ("native_pptx", "publication_status"), status)

    decision = evaluate_template_v2_publication(assets)

    assert decision.publishable is False
    assert decision.reason_code == "template_v2_publication_not_approved"


@pytest.mark.parametrize("explicit", [False, "true", 1, 0])
def test_publication_rejects_non_true_explicit_publishable_value(explicit: object):
    assets = _native_clone_assets()
    _set_path(assets, ("native_pptx", "publishable"), explicit)

    assert evaluate_template_v2_publication(assets).publishable is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("native_pptx", "schema_version"), "presenton.template-v2-native-source/v2"),
        (("native_pptx", "storage_key"), ""),
        (("native_pptx", "source_sha256"), "sha256:" + SHA256),
        (("native_pptx", "source_sha256"), "A" * 64),
        (("native_pptx", "source_size_bytes"), 0),
        (("native_pptx", "source_size_bytes"), True),
        (("native_pptx", "layout_source_parts"), {}),
        (
            ("native_pptx", "layout_source_parts"),
            {"title": "ppt/slides/slide0.xml"},
        ),
        (
            ("native_pptx", "layout_source_parts"),
            {
                "title": "ppt/slides/slide1.xml",
                "content": "ppt/slides/slide1.xml",
            },
        ),
    ],
)
def test_native_clone_rejects_malformed_source_evidence(
    path: tuple[str, ...], value: object
):
    assets = _native_clone_assets()
    _set_path(assets, path, value)

    decision = evaluate_template_v2_publication(assets)

    assert decision.publishable is False
    assert decision.reason_code == "template_v2_publication_evidence_invalid"


@pytest.mark.parametrize(
    ("capability", "value"),
    [
        ("native_clone", False),
        ("text_patch", None),
        ("table_patch", 1),
        ("master_layout_theme", "true"),
        ("relationships_media", False),
    ],
)
def test_native_clone_requires_each_capability_as_literal_true(
    capability: str, value: object
):
    assets = _native_clone_assets()
    _set_path(assets, ("native_pptx", "capabilities", capability), value)

    assert evaluate_template_v2_publication(assets).publishable is False


def test_native_clone_rejects_unversioned_extra_capability():
    assets = _native_clone_assets()
    _set_path(assets, ("native_pptx", "capabilities", "charts"), True)

    assert evaluate_template_v2_publication(assets).publishable is False


def test_reconstructed_publication_requires_successful_compiler_preflight():
    decision = evaluate_template_v2_publication(_reconstructed_assets())

    assert decision.publishable is True
    assert decision.reason_code == "template_v2_publication_ready"
    assert decision.preservation_mode == "reconstructed"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("native_pptx", "publishable"), False),
        (("native_pptx", "publishable"), 1),
        (("native_pptx", "schema_version"), "presenton.template-v2-publication/v2"),
        (("native_pptx", "native_pptx_preflight"), None),
        (
            ("native_pptx", "native_pptx_preflight", "schema_version"),
            "presenton.template-v2-native-pptx/v2",
        ),
        (
            ("native_pptx", "native_pptx_preflight", "compiler_name"),
            "unknown-compiler",
        ),
        (("native_pptx", "native_pptx_preflight", "compiler_version"), "2"),
        (
            ("native_pptx", "native_pptx_preflight", "preservation_mode"),
            "native_clone",
        ),
        (("native_pptx", "native_pptx_preflight", "status"), "unsupported"),
        (
            ("native_pptx", "native_pptx_preflight", "unsupported"),
            {"code": "template_v2_native_pptx_element_unsupported", "path": "x"},
        ),
        (
            ("native_pptx", "native_pptx_preflight", "structural_sha256"),
            SHA256,
        ),
        (
            ("native_pptx", "native_pptx_preflight", "package_sha256"),
            "sha256:" + "A" * 64,
        ),
    ],
)
def test_reconstructed_publication_rejects_incomplete_or_unsupported_preflight(
    path: tuple[str, ...], value: object
):
    assets = _reconstructed_assets()
    _set_path(assets, path, value)

    assert evaluate_template_v2_publication(assets).publishable is False


def test_unknown_preservation_mode_fails_closed():
    assets = _native_clone_assets()
    _set_path(assets, ("native_pptx", "preservation_mode"), "raster_fallback")

    decision = evaluate_template_v2_publication(assets)

    assert decision.publishable is False
    assert decision.reason_code == "template_v2_publication_mode_unsupported"


def test_top_level_publication_contract_remains_supported_for_reconstructed_rows():
    nested = _reconstructed_assets()["native_pptx"]

    decision = evaluate_template_v2_publication(nested)

    assert decision.publishable is True
