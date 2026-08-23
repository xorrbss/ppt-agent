from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from models.authored_vnext.semantic.contracts import (
    TextParagraphV1,
    TextPayloadV1,
    TextRunV1,
)
from models.authored_vnext.semantic.typography import (
    NativeTypographyReadbackV1,
    NativeTypographyReleaseEvidenceV1,
    NativeTypographyRunReadbackV1,
)
from models.sql.presentation_export_attempt import ExportPowerPointQaEvidence
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    canonical_digest,
)
from services.authored_vnext.semantic.readiness_validators import (
    validate_cjk_release_authority_evidence_v3,
)
from services.authored_vnext.semantic.typography import (
    NativeFontByteRegistry,
    admit_native_typography,
    load_bounded_windows_cjk_registry,
    native_typography_com_reopen_digest,
)


_FONT_DIR = Path(r"C:\Windows\Fonts")
pytestmark = pytest.mark.skipif(
    not all(
        (_FONT_DIR / name).is_file()
        for name in ("malgun.ttf", "batang.ttc", "consola.ttf")
    ),
    reason="bounded CJK release authority requires the reviewed Windows font bytes",
)


def _digest(seed: str) -> str:
    return sha256(seed.encode()).hexdigest()


def _run(text: str, family: str) -> TextRunV1:
    return TextRunV1(
        text=text,
        language="ko-KR",
        script="cjk",
        font_family=family,
        font_face="Regular",
        font_size_pt=18,
        weight=400,
        color="#102A56",
    )


def _qa(readback: str | None = None) -> ExportPowerPointQaEvidence:
    readback = readback or _digest("same-com-readback")
    rendered = _digest("same-com-render")
    return ExportPowerPointQaEvidence(
        status="passed",
        platform="windows",
        supported=True,
        automation="PowerPoint.COM",
        reopened=True,
        rendered=True,
        readback=True,
        verification_scope="powerpoint-first-open-save-reopen-stability",
        semantic_payload_binding="passed",
        repair_dialogs=0,
        repair_observation="interactive-observation",
        rendered_png_sha256=[rendered],
        first_open_rendered_png_sha256=[rendered],
        reopen_rendered_png_sha256=[rendered],
        render_hashes_match=True,
        first_open_readback_sha256=readback,
        reopen_readback_sha256=readback,
        first_open_readback_artifact="artifact://com/first-open-readback",
        reopen_readback_artifact="artifact://com/reopen-readback",
        readback_match=True,
        powerpoint_version="PowerPoint-16.0.19029",
        slide_count=1,
        native_text=1,
    )


def _authority_fixture() -> tuple[dict, ExportPowerPointQaEvidence, NativeFontByteRegistry]:
    registry = load_bounded_windows_cjk_registry()
    runs = [
        _run("한국 AI 2026", "Malgun Gothic"),
        _run("전략과 실행", "Batang"),
        _run("코드 123", "BatangChe"),
        _run("CODE 2026", "Consolas"),
    ]
    element_id = "text.cjk.matrix"
    readback = NativeTypographyReadbackV1(
        element_id=element_id,
        runs=[
            NativeTypographyRunReadbackV1(
                run_index=index,
                text_digest=canonical_digest({"text": run.text}),
                face=run.font_family,
                east_asian_typeface=run.font_family,
            )
            for index, run in enumerate(runs)
        ],
        line_rect_digests=[canonical_digest({"elementId": element_id, "line": 0})],
        overflow=False,
    )
    admission = admit_native_typography(
        element_id=element_id,
        payload=TextPayloadV1(paragraphs=[TextParagraphV1(runs=runs)]),
        registry=registry,
        readback=readback,
    )
    com_reopen_digest = native_typography_com_reopen_digest(
        font_registry_digest=registry.digest,
        admission_receipts=[admission],
    )
    qa = _qa(com_reopen_digest.removeprefix("sha256:"))
    authority = NativeTypographyReleaseEvidenceV1(
        font_registry_digest=registry.digest,
        font_faces=list(registry.receipts),
        admission_receipts=[admission],
        readback_authority="PowerPoint.COM-reopen",
        powerpoint_qa_digest=canonical_digest(qa),
        first_open_readback_digest=f"sha256:{qa.first_open_readback_sha256}",
        reopen_readback_digest=f"sha256:{qa.reopen_readback_sha256}",
    )
    embedded_faces = [
        {
            "typeface": face.family,
            "face": face.face.casefold(),
            "sourceSha256": face.font_byte_digest.removeprefix("sha256:"),
            "sourceBytes": face.size_bytes,
            "embeddedBytes": face.size_bytes,
            "fsType": face.fs_type,
            "licenseDecision": "allowed-editable",
            "subset": False,
        }
        for face in registry.receipts
    ]
    quality = {
        "fontEmbeddingStatus": {
            "requested": True,
            "applied": True,
            "nativeTextUsage": "used",
            "nativeTextElementsUsingEmbeddedTypefaces": 1,
            "nativeTypefaceFamilies": [face.family for face in registry.receipts],
            "faces": embedded_faces,
            "failures": [],
        },
        "nativeTypographyEvidence": authority.model_dump(mode="json", by_alias=True),
    }
    return quality, qa, registry


def _validate(quality: dict, qa: ExportPowerPointQaEvidence, registry) -> None:
    validate_cjk_release_authority_evidence_v3(
        quality=quality,
        qa=qa,
        declared_element_ids={"text.cjk.matrix"},
        native_outcomes={"text.cjk.matrix": "native"},
        target=SimpleNamespace(
            environment=SimpleNamespace(font_inventory_digest=registry.digest)
        ),
        registry=registry,
    )


def test_actual_font_bytes_admission_and_com_reopen_bind_cjk_authority() -> None:
    quality, qa, registry = _authority_fixture()
    _validate(quality, qa, registry)


def test_fake_noto_sidecar_cannot_issue_cjk_authority() -> None:
    registry = load_bounded_windows_cjk_registry()
    fake_quality = {
        "fontEmbeddingStatus": {
            "requested": True,
            "applied": True,
            "nativeTextUsage": "used",
            "faces": [
                {
                    "typeface": "Noto Serif CJK KR",
                    "face": "regular",
                    "sourceSha256": _digest("font"),
                    "sourceBytes": 2048,
                    "fsType": 8,
                    "licenseDecision": "allowed-editable",
                }
            ],
        }
    }
    with pytest.raises(SemanticContractError) as caught:
        _validate(fake_quality, _qa(), registry)
    assert caught.value.code == "authored_trusted_cjk_native_evidence_invalid"


def test_tampered_font_bytes_and_raster_fallback_evidence_fail_closed() -> None:
    quality, qa, registry = _authority_fixture()
    quality["fontEmbeddingStatus"]["faces"][0]["sourceSha256"] = _digest("tampered")
    with pytest.raises(SemanticContractError) as caught:
        _validate(quality, qa, registry)
    assert caught.value.code == "authored_trusted_cjk_font_embedding_evidence_mismatch"

    missing_registry = NativeFontByteRegistry()
    run = _run("한국 AI 2026", "Malgun Gothic")
    fallback = admit_native_typography(
        element_id="text.cjk.fallback",
        payload=TextPayloadV1(paragraphs=[TextParagraphV1(runs=[run])]),
        registry=missing_registry,
        readback=None,
    )
    with pytest.raises(ValidationError):
        NativeTypographyReleaseEvidenceV1(
            font_registry_digest=missing_registry.digest,
            font_faces=list(registry.receipts),
            admission_receipts=[fallback],
            readback_authority="PowerPoint.COM-reopen",
            powerpoint_qa_digest=canonical_digest(qa),
            first_open_readback_digest=f"sha256:{qa.first_open_readback_sha256}",
            reopen_readback_digest=f"sha256:{qa.reopen_readback_sha256}",
        )
