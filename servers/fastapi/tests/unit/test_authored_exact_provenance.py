from copy import deepcopy
from dataclasses import replace

import pytest

from services.authored_exact_provenance import (
    ExactProvenanceError,
    build_exact_provenance,
    verify_exact_provenance,
)
from utils.authored_styles import AuthoredStyle
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION


def _style(tmp_path, sources=(b"source-zero", b"source-one")) -> AuthoredStyle:
    paths = tuple(
        tmp_path / f"private-source-{index}.png"
        for index in range(len(sources))
    )
    for path, source in zip(paths, sources):
        path.write_bytes(source)
    return AuthoredStyle(
        id="exact-private",
        name="Exact private",
        description="Test-only exact style",
        preview_bg="#FFFFFF",
        preview_accent="#111111",
        brief="Preserve every source detail.",
        reference_mode=EXACT_RECONSTRUCTION,
        slide_reference_images=paths,
        slide_reference_indices=tuple(range(len(paths))),
    )


def test_exact_provenance_round_trips_without_paths_or_bytes(tmp_path):
    style = _style(tmp_path)

    provenance, sources = build_exact_provenance(style)

    assert verify_exact_provenance(
        provenance, style, expected_slide_count=2
    ) == sources
    serialized = str(provenance)
    assert str(tmp_path) not in serialized
    assert "private-source" not in serialized
    assert "source-zero" not in serialized
    assert [item["slide_index"] for item in provenance["sources"]] == [0, 1]


def test_exact_provenance_binds_private_snapshot_identity(tmp_path):
    style = _style(tmp_path)
    snapshot_id = "a" * 32
    provenance, sources = build_exact_provenance(
        style,
        source_snapshot_id=snapshot_id,
    )

    assert provenance["source_snapshot_id"] == snapshot_id
    assert verify_exact_provenance(
        provenance, style, expected_slide_count=2
    ) == sources

    changed = deepcopy(provenance)
    changed["source_snapshot_id"] = "b" * 32
    with pytest.raises(ExactProvenanceError, match="manifest changed"):
        verify_exact_provenance(changed, style, expected_slide_count=2)


@pytest.mark.parametrize(
    "mutation, expected_message",
    [
        (
            lambda value: value.update(
                {"reference_mode": {"private": "C:/secret/source.png"}}
            ),
            "mode is invalid",
        ),
        (lambda value: value["sources"].pop(), "source count changed"),
        (
            lambda value: value["sources"].reverse(),
            "source order changed",
        ),
        (
            lambda value: value["sources"][0].update({"sha256": "0" * 64}),
            "source bytes changed",
        ),
        (
            lambda value: value["sources"][0].update({"sha256": "z" * 64}),
            "source digest is invalid",
        ),
        (
            lambda value: value.update({"preset_revision": "0" * 64}),
            "preset revision changed",
        ),
        (
            lambda value: value.update({"manifest_fingerprint": "0" * 64}),
            "manifest changed",
        ),
    ],
)
def test_exact_provenance_fails_closed_for_saved_manifest_mismatch(
    tmp_path, mutation, expected_message
):
    style = _style(tmp_path)
    provenance, _ = build_exact_provenance(style)
    changed = deepcopy(provenance)
    mutation(changed)

    with pytest.raises(ExactProvenanceError, match=expected_message) as exc_info:
        verify_exact_provenance(changed, style, expected_slide_count=2)

    assert str(tmp_path) not in str(exc_info.value)
    assert provenance["sources"][0]["sha256"] not in str(exc_info.value)


def test_exact_provenance_detects_current_source_and_preset_changes(tmp_path):
    style = _style(tmp_path)
    provenance, _ = build_exact_provenance(style)
    style.slide_reference_images[0].write_bytes(b"replacement")

    with pytest.raises(ExactProvenanceError, match="source bytes changed"):
        verify_exact_provenance(provenance, style, expected_slide_count=2)

    style = _style(tmp_path)
    provenance, _ = build_exact_provenance(style)
    revised_style = replace(style, brief="A different exact preset revision.")
    with pytest.raises(ExactProvenanceError, match="preset revision changed"):
        verify_exact_provenance(
            provenance, revised_style, expected_slide_count=2
        )
