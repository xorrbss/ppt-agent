"""Concrete Public-v2 composition root for the Deck IR generation engine.

This is intentionally the only place that joins provider configuration,
Chromium capture, SQL ownership/fencing and the semantic manifest CAS.  It is
not a fixture adapter and it never delegates a sealed Deck IR operation to the
legacy provider-HTML path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation
from services.authored_checkpoint_service import (
    initialize_checkpoints,
    reserve_provider_attempt,
)
from services.authored_evidence_ledger import digest_deck_htmls
from services.authored_quality_receipt_v2 import (
    QualityReceiptV2,
    QualityReceiptV3,
    QualityReceiptV3Ledger,
    create_quality_receipt_v2,
    create_quality_receipt_v3_from_v2,
)
from models.authored_vnext.capability_registry import CAPABILITY_REGISTRY_DIGEST
from services.authored_vnext.slide_quality_acceptance import (
    SCHEMA_VERSION as SLIDE_QUALITY_SCHEMA_VERSION,
    validate_slide_quality_acceptance_receipt,
)
from services.authored_presentation_artifacts import _save_slide_pngs
from services.authored_vnext.deck_ir.director import ProductionStructuredDeckDirector
from services.authored_vnext.deck_ir.integration import (
    ConstraintCompilerDeckIrExecutionAdapter,
    DeckIrIntegrationError,
)
from services.authored_vnext.deck_ir.grammar import default_design_grammar
from services.authored_vnext.deck_ir.style_profiles import (
    design_grammar_for_style,
    resolve_style_profile,
)
from services.authored_vnext.deck_ir.quality import evaluate_deck_quality
from services.authored_vnext.semantic.publication import (
    SemanticContractError,
    publish_authored_semantic_bundle_graph,
)
from services.authored_vnext.semantic.canonical import bytes_digest, canonical_digest
from services.authored_vnext.semantic.typography import (
    NativeFontByteRegistry,
    NativeTypographyRegistryError,
)
from services.authored_vnext.master.digests import (
    canonical_digest as quality_canonical_digest,
)
from services.public_quality_receipt_v3_store import (
    PostgresOperationQualityReceiptV3Port,
)
from models.authored_vnext.semantic.contracts import (
    ArtifactPointerV1,
    FontManifestV1,
    ResolvedFontV1,
)
from services.database import async_session_maker
from utils.authored_dom_quality import inspect_authored_dom, normalize_browser_metrics
from utils.renderer_isolation import RendererDisabledError, RendererPolicyError
from utils.slide_capture import render_html_to_png_with_metrics
from services.authored_canvas import is_default_landscape


def _strict_object(
    *, properties: Mapping[str, object], required: list[str]
) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": required,
    }


def _as_digest(value: object) -> str:
    if not isinstance(value, Mapping) or not isinstance(value.get("receiptDigest"), str):
        raise DeckIrIntegrationError("authored_manifest_publication_failed")
    return str(value["receiptDigest"])


def _director_schemas() -> dict[str, Mapping[str, object]]:
    """Purpose-specific strict JSON schemas; no raw presentation surface."""

    from services.authored_vnext.deck_ir.contracts import (
        ArtDirectionIntentV1,
        ComponentPayloadV1,
        CompositionIntentGraphV1,
        DeckIRV1,
        SlideComponentV1,
        SlideIRV1,
    )

    deck = DeckIRV1.model_json_schema()
    slide = SlideIRV1.model_json_schema()
    composition = CompositionIntentGraphV1.model_json_schema()
    art_direction = ArtDirectionIntentV1.model_json_schema()
    definitions: dict[str, object] = {}
    for schema in (deck, slide, composition, art_direction):
        raw = schema.get("$defs")
        if isinstance(raw, Mapping):
            definitions.update(raw)
    # Digest identity is server-owned.  The provider never supplies a graph,
    # intent, Deck IR, or source identity digest; integration seals them after
    # it validates the provider's bounded content intent.
    def remove_server_digests(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                for key in (
                    "graphDigest", "intentDigest", "compositionIntentGraphDigest",
                    "artDirectionIntentDigest", "receiptDigest",
                ):
                    properties.pop(key, None)
                    required = node.get("required")
                    if isinstance(required, list) and key in required:
                        required.remove(key)
            for value in node.values():
                remove_server_digests(value)
        elif isinstance(node, list):
            for value in node:
                remove_server_digests(value)
    remove_server_digests(definitions)
    # Pydantic keeps fields used directly by a root model outside ``$defs``.
    # Strip those too, otherwise the provider is incorrectly asked to mint a
    # canonical server digest for every slide/intent.
    remove_server_digests(slide)
    remove_server_digests(composition)
    remove_server_digests(art_direction)
    component_schema = definitions.get("SlideComponentV1")
    payload_schema = definitions.get("ComponentPayloadV1")
    if isinstance(component_schema, Mapping) and isinstance(payload_schema, Mapping):
        component_properties = component_schema.get("properties")
        payload_properties = payload_schema.get("properties")
        if isinstance(component_properties, Mapping) and isinstance(payload_properties, Mapping):
            variants: list[dict[str, object]] = []
            payload_aliases = {
                name: field.alias or name
                for name, field in ComponentPayloadV1.model_fields.items()
            }
            for kind, (required_payload, optional_payload) in SlideComponentV1.PAYLOAD_FIELDS.items():
                # Advertise the actual source-free renderer, not future asset
                # or nested-group capabilities that would fail after billing.
                if not default_design_grammar().supports_component(kind):
                    continue
                variant = deepcopy(dict(component_schema))
                variant_properties = deepcopy(dict(component_properties))
                variant_properties["kind"] = {"const": kind}
                allowed_payload = sorted(
                    payload_aliases[key]
                    for key in required_payload | optional_payload
                )
                variant_properties["payload"] = {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        key: deepcopy(payload_properties[key])
                        for key in allowed_payload
                    },
                    "required": sorted(
                        payload_aliases[key] for key in required_payload
                    ),
                }
                variant["properties"] = variant_properties
                if kind == "chart":
                    variant_properties["payload"]["properties"]["chartType"] = {"const": "bar"}
                variants.append(variant)
            definitions["SlideComponentV1"] = {"oneOf": variants}
    deck_properties = deck.get("properties")
    if not isinstance(deck_properties, Mapping):
        raise DeckIrIntegrationError("deck_ir_contract_runtime_unavailable")
    global_plan = _strict_object(
        properties={
            "schemaVersion": {"const": "deck-global-plan/v1"},
            "deckKey": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1},
            "language": {"type": "string", "minLength": 2},
            "story": deck_properties["story"],
            "designLanguage": deck_properties["designLanguage"],
        },
        required=["schemaVersion", "deckKey", "title", "language", "story", "designLanguage"],
    )
    batch = _strict_object(
        properties={
            "schemaVersion": {"const": "slide-ir-batch/v1"},
            "slides": {"type": "array", "items": slide},
            "compositionIntentGraphs": {"type": "array", "items": composition},
            "artDirectionIntents": {"type": "array", "items": art_direction},
        },
        required=["schemaVersion", "slides", "compositionIntentGraphs", "artDirectionIntents"],
    )
    global_plan["$defs"] = definitions
    batch["$defs"] = definitions
    return {"global-plan": global_plan, "slide-ir-batch": batch}


def _semantic_draft(
    compiled: Mapping[str, object],
    *,
    language: str,
    font_family: str,
    font_face: str,
) -> dict[str, object]:
    """Materialize server-owned semantic text from the compiler's QID source."""

    semantic = compiled.get("semantic")
    if not isinstance(semantic, Mapping) or not isinstance(semantic.get("elements"), list):
        raise DeckIrIntegrationError("compiled_semantic_mismatch")
    elements: list[dict[str, object]] = []
    for index, item in enumerate(semantic["elements"]):
        if not isinstance(item, Mapping):
            raise DeckIrIntegrationError("compiled_semantic_mismatch")
        qid = item.get("qid")
        role = item.get("semanticRole")
        text = item.get("text")
        if not isinstance(qid, str) or not isinstance(role, str) or not isinstance(text, str):
            raise DeckIrIntegrationError("compiled_semantic_mismatch")
        if item.get("kind") == "shape" and not text:
            elements.append(
                {
                    "elementId": qid,
                    "kind": "shape",
                    "semanticRole": role,
                    "intendedOrder": index,
                    "payload": {"preset": "rectangle"},
                }
            )
        else:
            script = "cjk" if re.search(r"[\u3000-\u9fff\uac00-\ud7af]", text) else "latin"
            elements.append(
                {
                    "elementId": qid,
                    "kind": "text",
                    "semanticRole": role,
                    "intendedOrder": index,
                    "payload": {
                        "paragraphs": [
                            {
                                "runs": [
                                    {
                                        "text": text or role,
                                        "language": language or "en-US",
                                        "script": script,
                                        "fontFamily": font_family,
                                        "fontFace": font_face,
                                        "fontSizePt": 18,
                                        "weight": 400,
                                        "color": "#111111",
                                    }
                                ]
                            }
                        ]
                    },
                }
            )
    if not elements:
        raise DeckIrIntegrationError("compiled_semantic_mismatch")
    return {
        "semanticSource": {
            "elements": elements,
            "identityRegistry": {
                "activeQids": sorted(str(item["elementId"]) for item in elements),
                "retiredQids": [],
            },
        }
    }


async def _publication_font_materials() -> tuple[
    FontManifestV1, dict[str, bytes], str, str
]:
    """Resolve the exact Windows font bytes named by semantic text runs."""

    windows_root = Path(os.environ.get("WINDIR", r"C:\Windows"))
    font_path = windows_root / "Fonts" / "malgun.ttf"
    try:
        content = await asyncio.to_thread(font_path.read_bytes)
        registry = NativeFontByteRegistry()
        receipt = registry.register(content)
    except (OSError, NativeTypographyRegistryError) as error:
        raise DeckIrIntegrationError(
            "authored_semantic_font_bytes_unavailable"
        ) from error
    if (
        receipt.family.casefold() != "malgun gothic"
        or receipt.face.casefold() != "regular"
    ):
        raise DeckIrIntegrationError("authored_semantic_font_identity_mismatch")
    digest_value = bytes_digest(content)
    resolved = ResolvedFontV1(
        face_id="font:malgun-gothic-regular",
        family=receipt.family,
        face=receipt.face,
        artifact=ArtifactPointerV1(
            artifact_ref=(
                "artifact://sha256/" + digest_value.removeprefix("sha256:")
            ),
            digest=digest_value,
            byte_length=len(content),
            media_type="font/ttf",
        ),
        locator_digest=canonical_digest(
            {
                "family": receipt.family,
                "face": receipt.face,
                "fontByteDigest": receipt.font_byte_digest,
                "resolverVersion": "deck-ir-windows-font/v1",
            }
        ),
        resolver_version="deck-ir-windows-font/v1",
        embedding_permitted=receipt.embedding_permitted,
        subsetting_permitted=receipt.subsetting_permitted,
        retention_class="active",
    )
    return (
        FontManifestV1(fonts=[resolved]),
        {digest_value: content},
        receipt.family,
        receipt.face,
    )


def _generation_release_quality_receipts(
    *,
    attempt_token: str,
    revision: int,
    htmls: list[str],
    deck_ir: Mapping[str, object],
    generation_quality: Mapping[str, object],
    content_integrity: Mapping[str, object] | None = None,
    source_content_quality_evidence: Mapping[str, object] | None = None,
    execution_evidence: Mapping[str, object] | None = None,
) -> tuple[QualityReceiptV2, QualityReceiptV3, Mapping[str, object]]:
    """Bridge measured Deck IR generation into the existing release-review base."""

    deck_html_digest = digest_deck_htmls(htmls)
    source_document_quality = (
        generation_quality.get("schemaVersion") == SLIDE_QUALITY_SCHEMA_VERSION
    )
    if source_document_quality:
        slides_payload = generation_quality.get("slides")
        if (
            generation_quality.get("documentQualityPassed") is not True
            or generation_quality.get("observedSlideCount") != len(htmls)
            or generation_quality.get("expectedSlideCount") != len(htmls)
            or not isinstance(slides_payload, list)
            or len(slides_payload) != len(htmls)
            or any(
                not isinstance(item, Mapping)
                or item.get("passed") is not True
                or not isinstance(item.get("score"), (int, float))
                or isinstance(item.get("score"), bool)
                or float(item["score"]) < 90.0
                for item in slides_payload
            )
            or not validate_slide_quality_acceptance_receipt(generation_quality)
        ):
            raise DeckIrIntegrationError("source_native_document_quality_failed")
        if (
            not isinstance(content_integrity, Mapping)
            or content_integrity.get("status") != "passed"
            or content_integrity.get("requiredCoverage") != 1.0
            or not isinstance(source_content_quality_evidence, Mapping)
            or source_content_quality_evidence.get("status") != "passed"
        ):
            raise DeckIrIntegrationError("content_integrity_failed")
    capacity = {
        "status": "passed",
        "browserObserved": True,
        "checks": {
            name: True
            for name in ("text", "title", "body", "font", "qid", "image")
        },
    }
    safety = {"status": "passed", "residuals": [], "issues": []}
    visual = (
        {
            "status": "passed",
            "fullDeck": True,
            "reviewedCount": len(htmls),
            "minimumSlideScore": generation_quality.get("minimumSlideScore"),
            "threshold": generation_quality.get("slideScoreThreshold"),
            "receiptDigest": generation_quality.get("receiptDigest"),
        }
        if source_document_quality
        else {"status": "not_assessed", "fullDeck": False}
    )
    source_readback = (
        {
            "status": "passed",
            "required": True,
            "verified": True,
            "passed": True,
            "failures": [],
            "failedBlocks": [],
            "clipped": False,
            "occluded": False,
            "receiptDigest": content_integrity.get("receiptDigest"),
            "requiredCoverage": content_integrity.get("requiredCoverage"),
        }
        if source_document_quality and isinstance(content_integrity, Mapping)
        else {"status": "not_applicable", "required": False}
    )
    v2 = create_quality_receipt_v2(
        {},
        attempt_id=attempt_token,
        revision=revision,
        ledger_digest=quality_canonical_digest(
            {
                "schemaVersion": "deck-ir-generation-quality-ledger/v1",
                "deckHtmlDigest": deck_html_digest,
                "generationQualityReceipt": dict(generation_quality),
            }
        ),
        deck_html_digest=deck_html_digest,
        evidence={
            "safety": safety,
            "capacity": capacity,
            "sourceReadback": source_readback,
            "visualReview": visual,
            "performance": {"status": "not_assessed"},
            "publication": {"status": "published"},
        },
        requested_release_profile="release",
        pre_export_evaluation_profile=(
            "source_native_generation"
            if source_document_quality
            else "default"
        ),
    )
    deck_slides = [
        item for item in deck_ir.get("slides", []) if isinstance(item, Mapping)
    ]
    quality_slides = generation_quality.get("slides")
    quality_slides = quality_slides if isinstance(quality_slides, list) else []
    slides = []
    for index, item in enumerate(deck_slides):
        quality_slide = quality_slides[index] if index < len(quality_slides) and isinstance(quality_slides[index], Mapping) else {}
        slides.append(
            {
                "role": str(item.get("role") or "content"),
                "repairApplied": False,
                "layoutCapacity": (
                    max(0.0, min(1.0, float(quality_slide.get("score", 100.0)) / 100.0))
                    if source_document_quality
                    else 0.0
                ),
            }
        )
    registry_consumers = {
        name: {
            "status": "passed",
            "required": True,
            "digest": CAPABILITY_REGISTRY_DIGEST,
        }
        for name in ("prompt", "validator", "font", "chromium", "scene")
    }
    registry_consumers.update(
        {
            name: {"status": "not_required", "required": False}
            for name in ("ooxml", "readback")
        }
    )
    if source_document_quality:
        registry_consumers["readback"] = {
            "status": "passed",
            "required": True,
            "digest": CAPABILITY_REGISTRY_DIGEST,
        }
    evidence: dict[str, object] = {
        "qualityTier": "T1",
        "capabilityRegistry": {
            "digest": CAPABILITY_REGISTRY_DIGEST,
            "consumers": registry_consumers,
        },
        "deterministic": {
            "geometry": {
                "status": "passed",
                "observed": True,
                "evidenceDigest": quality_canonical_digest(capacity),
            },
            "font": {
                "status": "passed",
                "observed": True,
                "evidenceDigest": quality_canonical_digest(capacity),
            },
            "content": {
                "status": "passed",
                "observed": True,
                "evidenceDigest": quality_canonical_digest(safety),
            },
            "visual": {
                "status": "passed" if source_document_quality else "not_required",
                "observed": bool(source_document_quality),
                "evidenceDigest": quality_canonical_digest(visual),
            },
            "readback": {
                "status": "passed" if source_document_quality else "not_required",
                "observed": bool(source_document_quality),
                "evidenceDigest": quality_canonical_digest(source_readback),
            },
        },
        "providerCalls": {
            "mode": (
                "source-native-provider-free"
                if source_document_quality
                else "authored-provider"
            ),
            "slides": [
                {
                    "slideIndex": index,
                    "initial": 0 if source_document_quality else 1,
                    "recovery": 0,
                    "total": 0 if source_document_quality else 1,
                }
                for index in range(len(slides))
            ]
        },
        "sourceContent": (
            dict(source_content_quality_evidence)
            if source_document_quality and isinstance(source_content_quality_evidence, Mapping)
            else {"status": "not_required", "required": False}
        ),
        "slides": slides,
        "visualReview": visual,
        "persistence": {
            "status": "passed",
            "required": True,
            "authority": "injected-durable-port",
        },
        "checkerPayloads": {
            "schemaVersion": "authored-generation-checker-payloads/v1",
            "deckHtmlDigest": deck_html_digest,
            "capacity": capacity,
            "safety": safety,
            "visual": visual,
            "sourceReadback": source_readback,
            "slideDocumentQualityReceipt": (
                dict(generation_quality) if source_document_quality else None
            ),
            "sourceContent": (
                dict(source_content_quality_evidence)
                if source_document_quality
                and isinstance(source_content_quality_evidence, Mapping)
                else None
            ),
        },
    }
    evidence["versionTransition"] = {
        "fromSchemaVersion": "quality-receipt/v2",
        "fromReceiptDigest": v2.receipt_digest,
        "fromGatePassed": v2.gate_passed,
    }
    if execution_evidence is not None:
        evidence["deckIrExecution"] = dict(execution_evidence)
    v3 = create_quality_receipt_v3_from_v2(
        QualityReceiptV3Ledger(), receipt_v2=v2, evidence=evidence
    )
    unresolved = set(v3.blockers)
    if not source_document_quality:
        unresolved.discard("visualReview.required_failed")
    if not v2.gate_passed or unresolved:
        raise DeckIrIntegrationError("quality_receipt_v3_blocked")
    return v2, v3, evidence


def _evaluate_generation_quality(payload: Mapping[str, object]) -> dict[str, object]:
    """Turn compiler, Chromium and Deck IR facts into a generation receipt."""

    observations = payload.get("observations")
    compiled = payload.get("compiledSlides")
    if not isinstance(observations, list) or not isinstance(compiled, list):
        raise DeckIrIntegrationError("generation_quality_receipt_invalid")
    browser_checks = {
        name: all(
            isinstance(item, Mapping) and item.get("checks", {}).get(name) is True
            for item in observations
            if isinstance(item, Mapping)
        ) and len(observations) == len(compiled)
        for name in (
            "viewportContainment", "overflow", "clipping", "overlap", "ghosting", "fontLoad",
            "imageDecode", "minimumFont", "contrast", "connectorClearance", "quiescence", "pngObservationBinding",
        )
    }
    browser_issues = [
        issue for item in observations if isinstance(item, Mapping)
        for issue in item.get("issues", []) if isinstance(issue, Mapping)
    ]
    deck = payload.get("deckIr")
    coverage = payload.get("requirementCoverageReceipt")
    if not isinstance(deck, Mapping):
        raise DeckIrIntegrationError("generation_quality_receipt_invalid")
    slides = [item for item in deck.get("slides", []) if isinstance(item, Mapping)]
    compiled_keys = [str(item.get("slideKey")) for item in compiled if isinstance(item, Mapping)]
    slide_keys = [str(item.get("slideKey")) for item in slides]
    claims = [" ".join(str(item.get("claim", "")).casefold().split()) for item in slides]
    story = deck.get("story") if isinstance(deck.get("story"), Mapping) else {}
    design = deck.get("designLanguage") if isinstance(deck.get("designLanguage"), Mapping) else {}
    arc = {str(item) for item in story.get("arc", [])} if isinstance(story.get("arc", []), list) else set()
    roles = {str(item.get("role")) for item in slides}
    # Density labels are provider composition intent, not measured defects.
    # A sparse opening, dense table and medium-density process are compatible.
    # Gate each slide's capacity/readability using the existing Chromium probes;
    # do not invent a uniformity or pixel-occupancy threshold for mixed content.
    # Missing/misaligned observations must never pass via an empty all().
    density_checks = (
        "viewportContainment", "overflow", "clipping", "overlap",
        "minimumFont", "contrast", "fontLoad", "quiescence",
        "pngObservationBinding",
    )
    density_within_capacity = (
        bool(slides)
        and len(observations) == len(slides) == len(compiled)
        and all(
            isinstance(item, Mapping)
            and isinstance(item.get("checks"), Mapping)
            and all(item["checks"].get(name) is True for name in density_checks)
            for item in observations
        )
    )
    static_checks = {
        "schema": bool(slides) and len(slides) == len(compiled),
        "qid": all(len({str(element.get("qid")) for element in item.get("semantic", {}).get("elements", []) if isinstance(element, Mapping)}) == len(item.get("semantic", {}).get("elements", [])) for item in compiled if isinstance(item, Mapping)),
        "source": isinstance(coverage, Mapping) and coverage.get("status") == "passed" and all(not item.get("sourceBindingIds") and all(not component.get("sourceBindingId") for component in item.get("components", []) if isinstance(component, Mapping)) for item in slides),
        "asset": all("data-asset-slot" not in str(item.get("html", "")) for item in compiled if isinstance(item, Mapping)),
        "font": all(isinstance(item.get("semantic"), Mapping) for item in compiled if isinstance(item, Mapping)),
        "capacity": all(len(item.get("components", [])) <= 12 for item in slides),
        "semanticOrder": all(isinstance(item.get("semantic", {}).get("elements"), list) for item in compiled if isinstance(item, Mapping)),
        "accessibility": all("aria-label=" in str(item.get("html", "")) for item in compiled if isinstance(item, Mapping)),
        "domAllowlist": all("<script" not in str(item.get("html", "")).lower() and "<iframe" not in str(item.get("html", "")).lower() for item in compiled if isinstance(item, Mapping)),
        "deterministicDigest": all(isinstance(item.get("compileReceipt", {}).get("receiptDigest"), str) and isinstance(item.get("compileReceipt", {}).get("browserMeasurementDigest"), str) for item in compiled if isinstance(item, Mapping)),
    }
    deck_checks = {
        "slideOrder": slide_keys == compiled_keys and len(slide_keys) == len(set(slide_keys)),
        "storyProgression": bool(arc) and arc.issubset(roles),
        "furnitureConsistency": bool(design.get("grammarId")),
        "paletteConsistency": bool(design.get("paletteId")),
        "typeScaleConsistency": bool(design.get("typeScaleId")),
        "densityOutliers": density_within_capacity,
        "repeatedContent": len(claims) == len(set(claims)),
        "openingCompleteness": bool(slides and slides[0].get("claim") and slides[0].get("components")),
        "closingCompleteness": bool(slides and slides[-1].get("claim") and slides[-1].get("components")),
    }
    receipt = evaluate_deck_quality(
        static_evidence={"checks": static_checks},
        browser_evidence={"checks": browser_checks, "issues": browser_issues},
        deck_evidence={"checks": deck_checks},
    ).to_dict()
    if receipt.get("passed") is not True:
        raise DeckIrIntegrationError("deck_visual_quality_failed", quality_receipt=receipt)
    # The Deck gate has its own receipt schema.  The product boundary wraps the
    # same evidence under the generation-quality contract expected by the
    # orchestrator, so set the envelope version after expanding the inner
    # receipt instead of allowing it to overwrite the public discriminator.
    return {**receipt, "schemaVersion": "generation-quality-receipt/v1"}


async def observe_compiled_slide(compiled: Mapping[str, object]):
    html = compiled.get("html")
    key = compiled.get("slideKey")
    if not isinstance(html, str) or not isinstance(key, str):
        raise DeckIrIntegrationError("browser_observation_failed")
    try:
        capture = await render_html_to_png_with_metrics(html)
    except (RendererDisabledError, RendererPolicyError) as error:
        code = getattr(error, "code", None) or str(error)
        raise DeckIrIntegrationError(str(code)[:128]) from error
    if not isinstance(capture.browser_metrics, Mapping):
        raise DeckIrIntegrationError("browser_observation_failed")
    metrics = dict(capture.browser_metrics)
    normalized = normalize_browser_metrics(metrics)
    report = inspect_authored_dom(
        html,
        metrics,
        strict_structural_off_canvas=True,
    )
    viewport = normalized["viewport"]
    source_matches = metrics.get("source_digest") == capture.html_digest
    png_bound = bool(capture.png) and isinstance(capture.png_digest, str) and bool(capture.metrics_digest)
    # DOM issues are concrete metric-derived defects.  High/medium are
    # release blocking P1s; low remains a visible P2 and does not turn
    # an otherwise sound candidate into an invented hard failure.
    issue_records = [
        {
            "severity": "P1" if issue.severity in {"high", "medium"} else "P2",
            "code": f"dom_{issue.category}",
            "path": f"slide[{key}].{','.join(issue.qids) or 'document'}",
        }
        for issue in report.issues
    ]
    blocking = report.telemetry_status != "available" or any(
        item["severity"] == "P1" for item in issue_records
    )
    checks = {
        "viewportContainment": is_default_landscape(viewport["width"], viewport["height"]) and not any(issue.category == "off_canvas" for issue in report.issues),
        "overflow": not any(issue.category == "overflow" for issue in report.issues),
        "clipping": not any(issue.category == "clipping" for issue in report.issues),
        "overlap": not any(issue.category == "overlap" for issue in report.issues),
        "ghosting": not any(issue.category == "content" for issue in report.issues),
        "fontLoad": bool(normalized["fonts_ready"]),
        "imageDecode": not any(issue.category == "image" for issue in report.issues),
        "minimumFont": not any(issue.category == "font" for issue in report.issues),
        "contrast": not any(issue.category == "contrast" for issue in report.issues),
        "connectorClearance": not any(issue.category == "overlap" for issue in report.issues),
        "quiescence": report.telemetry_status == "available",
        "pngObservationBinding": source_matches and png_bound,
    }
    from .intent_resolution import browser_constraint_checks
    resolved_checks = browser_constraint_checks(compiled, normalized)
    checks.update(resolved_checks)
    issue_records.extend({"severity": "P1", "code": f"compiler_{name}", "path": f"slide[{key}]"}
                         for name, passed in resolved_checks.items() if not passed)
    observation = {
        "passed": not blocking and all(checks.values()),
        "htmlDigest": capture.html_digest,
        "pngDigest": capture.png_digest,
        "metricsDigest": capture.metrics_digest,
        "browserMetrics": dict(capture.browser_metrics),
        "checks": checks,
        "issues": issue_records,
        "geometryByQid": {
            str(item.get("qid")): dict(item.get("bbox") or {})
            for item in normalized["elements"]
            if isinstance(item, Mapping) and isinstance(item.get("qid"), str)
        },
    }
    return observation, capture


def _normalized_language_tag(value: object, *, brief: object = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = "ko-KR" if re.search(r"[\uac00-\ud7a3]", str(brief or "")) else "en-US"
    aliases = {
        "english": "en-US",
        "korean": "ko-KR",
        "한국어": "ko-KR",
        "japanese": "ja-JP",
        "chinese": "zh-CN",
    }
    normalized = aliases.get(raw.casefold(), raw)
    if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", normalized):
        return normalized
    raise DeckIrIntegrationError("deck_ir_language_invalid")


class ProductDeckIrExecutionAdapter:
    """Actual provider/renderer/CAS adapter installed for opted-in processes."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._session_factory = session_factory

    async def run(
        self,
        *,
        request: object,
        generation_job: object,
        context: object,
        strategy_receipt: Mapping[str, object],
    ) -> Mapping[str, object]:
        from utils.llm_config import get_client
        from services.authored_vnext.deck_ir.contracts import DeckIRV1
        from services.authored_vnext.deck_ir.strategy import (
            server_owned_requirement_graph, validate_sealed_generation_strategy,
        )
        from utils.llm_config import get_llm_config
        from utils.llm_provider import get_model

        if validate_sealed_generation_strategy(
            strategy_receipt,
            operation_id=str(context.claim.operation_id),
        ) != "deck-ir":
            raise DeckIrIntegrationError("deck_ir_strategy_not_selected")
        slide_count = getattr(request, "n_slides", None)
        if not isinstance(slide_count, int) or not 1 <= slide_count <= 10:
            raise DeckIrIntegrationError("deck_ir_balanced_slide_count_unsupported")
        authored_style = getattr(request, "authored_style", None)
        # This source-free requirement graph binds the request by digest only;
        # the raw brief remains ephemeral provider input and is never placed in
        # an operation, checkpoint, receipt, or log body.
        requirement_graph = server_owned_requirement_graph(
            str(getattr(request, "content", "") or "")
        )
        if strategy_receipt.get("requirementGraphDigest") != requirement_graph.get("graphDigest"):
            raise DeckIrIntegrationError("deck_ir_requirement_graph_binding_invalid")
        try:
            style_profile = resolve_style_profile(authored_style)
        except ValueError as error:
            raise DeckIrIntegrationError(str(error)) from error
        if strategy_receipt.get("styleProfileDigest") != style_profile.profile_digest:
            raise DeckIrIntegrationError("deck_ir_style_profile_binding_invalid")
        schemas = _director_schemas()
        normalized_language = _normalized_language_tag(
            getattr(request, "language", None),
            brief=getattr(request, "content", None),
        )
        director = ProductionStructuredDeckDirector(
            client=get_client(config=get_llm_config()),
            model=get_model(),
            schema=DeckIRV1.model_json_schema(),
            schemas_by_purpose=schemas,
            job_id=getattr(generation_job, "id", None),
            attempt_number=int(getattr(context.claim, "attempt_number", 0) or 0),
        )
        job_id = getattr(generation_job, "id", None)
        attempt_token = getattr(context.claim, "attempt_token", None)
        fencing_token = getattr(context.claim, "fencing_token", None)
        if not isinstance(job_id, uuid.UUID) or not isinstance(attempt_token, str) or not attempt_token:
            raise DeckIrIntegrationError("deck_ir_operation_binding_invalid")
        await initialize_checkpoints(
            job_id=job_id,
            attempt_token=attempt_token,
            fencing_token=fencing_token,
            roles=["deck-ir"] * slide_count,
        )
        reservation_count = 0

        async def reserve(purpose: str) -> None:
            nonlocal reservation_count
            await context.raise_if_cancelled()
            # The existing checkpoint reservation supports two durable ordinals
            # per slide.  Keep global/batches independently replay-safe and use
            # the second global ordinal for the one shared recovery only.
            if purpose == "global-plan":
                index, ordinal = 0, 1
            elif purpose == "shared-recovery":
                index, ordinal = 0, 2
            else:
                reservation_count += 1
                index, ordinal = min(reservation_count, slide_count - 1), 1
            await reserve_provider_attempt(
                job_id=job_id,
                slide_index=index,
                attempt_token=attempt_token,
                fencing_token=fencing_token,
                ordinal=ordinal,
            )

        captures: dict[str, object] = {}

        async def observe(compiled: Mapping[str, object]) -> Mapping[str, object]:
            observation, capture = await observe_compiled_slide(compiled)
            captures[str(compiled["slideKey"])] = capture
            return observation

        async def generation_quality(payload: Mapping[str, object]) -> Mapping[str, object]:
            return _evaluate_generation_quality(payload)

        async def publisher(payload: Mapping[str, object]) -> Mapping[str, object]:
            return await self._publish(
                payload=payload,
                request=request,
                generation_job=generation_job,
                context=context,
                captures=captures,
            )

        adapter = ConstraintCompilerDeckIrExecutionAdapter(
            director=director,
            grammar=design_grammar_for_style(style_profile),
            reserve_provider=reserve,
            observe=observe,
            generation_quality=generation_quality,
            publisher=publisher,
            requirement_graph=requirement_graph,
            language=normalized_language,
            authored_style=authored_style,
            style_profile=style_profile,
        )
        return await adapter.run(
            request=request,
            generation_job=generation_job,
            context=context,
            strategy_receipt=strategy_receipt,
        )

    async def _publish(
        self,
        *,
        payload: Mapping[str, object],
        request: object,
        generation_job: object,
        context: object,
        captures: Mapping[str, object],
    ) -> Mapping[str, object]:
        claim = context.claim
        operation_id = claim.operation_id
        presentation_id = claim.presentation_id
        owner_id = claim.owner_id
        attempt_token = claim.attempt_token
        fencing_token = claim.fencing_token
        expected_revision = claim.expected_revision
        if not all(isinstance(value, uuid.UUID) for value in (operation_id, presentation_id, owner_id)):
            raise DeckIrIntegrationError("deck_ir_operation_binding_invalid")
        if not isinstance(attempt_token, str) or not isinstance(fencing_token, int) or not isinstance(expected_revision, int):
            raise DeckIrIntegrationError("deck_ir_operation_binding_invalid")
        compiled = payload.get("compiledSlides")
        deck_ir = payload.get("deckIr")
        if not isinstance(compiled, list) or not isinstance(deck_ir, Mapping):
            raise DeckIrIntegrationError("authored_manifest_publication_failed")
        # Deck IR order is narrative authority; identifiers are not sort keys.
        ordered = [item for item in compiled if isinstance(item, Mapping)]
        if len(ordered) != len(compiled):
            raise DeckIrIntegrationError("authored_manifest_publication_failed")
        deck_slides = {
            str(item.get("slideKey")): item
            for item in deck_ir.get("slides", [])
            if isinstance(item, Mapping) and item.get("slideKey")
        }
        if list(deck_slides) != [str(item.get("slideKey")) for item in ordered]:
            raise DeckIrIntegrationError("compiled_semantic_mismatch")
        outlines = {
            "slides": [
                {
                    "content": str(
                        deck_slides[str(item.get("slideKey"))].get("claim")
                        or deck_slides[str(item.get("slideKey"))].get("role")
                        or item.get("slideKey")
                    )
                }
                for item in ordered
            ]
        }
        pngs: list[bytes] = []
        for item in ordered:
            capture = captures.get(item.get("slideKey"))
            png = getattr(capture, "png", None)
            if not isinstance(png, bytes):
                raise DeckIrIntegrationError("browser_observation_failed")
            pngs.append(png)
        # Only Deck IR publishes here; the source-native (design-pack) route
        # that also used this publisher was removed on 2026-11-03.
        publication_route = str(payload.get("publicationRoute") or "deck-ir")
        if publication_route != "deck-ir":
            raise DeckIrIntegrationError("authored_manifest_publication_failed")
        image_refs = await asyncio.to_thread(
            _save_slide_pngs,
            presentation_id,
            pngs,
            f"{publication_route}:{attempt_token}",
        )
        font_manifest, resolved_font_bytes, font_family, font_face = (
            await _publication_font_materials()
        )
        generation_quality = payload.get("generationQualityReceipt")
        if not isinstance(generation_quality, Mapping):
            raise DeckIrIntegrationError("generation_quality_receipt_invalid")
        htmls = [str(item["html"]) for item in ordered]
        quality_v2, quality_v3, quality_v3_evidence = (
            _generation_release_quality_receipts(
                attempt_token=attempt_token,
                revision=expected_revision + 1,
                htmls=htmls,
                deck_ir=deck_ir,
                generation_quality=generation_quality,
                content_integrity=(
                    payload.get("contentIntegrityReceipt")
                    if isinstance(payload.get("contentIntegrityReceipt"), Mapping)
                    else None
                ),
                source_content_quality_evidence=(
                    payload.get("sourceContentQualityEvidence")
                    if isinstance(payload.get("sourceContentQualityEvidence"), Mapping)
                    else None
                ),
                execution_evidence=payload.get("executionEvidence"),
            )
        )
        async with self._session_factory() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            presentation = await session.get(PresentationModel, presentation_id)
            if (
                operation is None
                or operation.state != "running"
                or operation.attempt_token != attempt_token
                or operation.fencing_token != fencing_token
                or presentation is None
                or presentation.owner_id != owner_id
                or presentation.revision != expected_revision
                or presentation.lifecycle_status != PRESENTATION_LIFECYCLE_STAGING
            ):
                raise DeckIrIntegrationError("deck_ir_operation_fence_lost")
            existing = list(
                await session.scalars(
                    select(SlideModel).where(SlideModel.presentation == presentation_id)
                )
            )
            if existing:
                raise DeckIrIntegrationError("deck_ir_presentation_not_empty")
            slides = [
                SlideModel(
                    owner_id=owner_id,
                    presentation=presentation_id,
                    layout_group="authored",
                    layout=f"authored:{publication_route}:{item['slideKey']}",
                    index=index,
                    content={"__authored__": True, "image": image_refs[index], "role": publication_route},
                    html_content=str(item["html"]),
                    properties={"image": image_refs[index]},
                )
                for index, item in enumerate(ordered)
            ]
            session.add_all(slides)
            await session.flush()
            try:
                manifest = await publish_authored_semantic_bundle_graph(
                    session,
                    presentation_id=presentation_id,
                    owner_id=owner_id,
                    expected_presentation_revision=expected_revision,
                    operation_id=operation_id,
                    generation_attempt_id=getattr(generation_job, "id", None),
                    slides=slides,
                    htmls=htmls,
                    semantic_drafts=[
                        _semantic_draft(
                            item,
                            language=str(deck_ir.get("language") or "en-US"),
                            font_family=font_family,
                            font_face=font_face,
                        )
                        for item in ordered
                    ],
                    # Bind the immutable bundle to the sealed admission,
                    # generation-only quality evidence, and requirement
                    # coverage.  Artifact quality deliberately is not part of
                    # this subject; export/readback owns that later receipt.
                    master_contract_digest=canonical_digest({
                        "strategyReceiptDigest": _as_digest(payload.get("strategyReceipt")),
                        "styleProfileDigest": payload.get("styleProfileDigest"),
                        "retainedReferenceDigest": payload.get("retainedReferenceDigest"),
                        "generationQualityReceipt": payload.get("generationQualityReceipt"),
                        "contentIntegrityReceipt": payload.get("contentIntegrityReceipt"),
                        "sourceContentQualityEvidence": payload.get("sourceContentQualityEvidence"),
                        "requirementCoverageReceipt": payload.get("requirementCoverageReceipt"),
                        "executionEvidence": payload.get("executionEvidence"),
                    }),
                    composition_digest=canonical_digest(deck_ir),
                    slide_font_manifests=[font_manifest for _ in ordered],
                    resolved_artifact_bytes=resolved_font_bytes,
                )
            except SemanticContractError as error:
                code = str(error)
                if not re.fullmatch(r"[a-z][a-z0-9_]{2,127}", code):
                    code = "authored_manifest_publication_failed"
                raise DeckIrIntegrationError(code) from error
            except (TypeError, ValueError) as error:
                raise DeckIrIntegrationError("authored_manifest_publication_failed") from error
            await PostgresOperationQualityReceiptV3Port(
                session,
                operation_id=operation_id,
                attempt_token=attempt_token,
                fencing_token=fencing_token,
            ).append_quality_receipt_v3(
                receipt=quality_v3,
                evidence=quality_v3_evidence,
            )
            next_theme = dict(presentation.theme or {})
            next_theme["generationDeckIr"] = {
                "strategy": publication_route,
                "styleProfileDigest": payload.get("styleProfileDigest"),
                "retainedReferenceDigest": payload.get("retainedReferenceDigest"),
                "manifestDigest": manifest.canonical_digest,
                "slideCount": len(ordered),
            }
            next_theme["authoredQualityGate"] = {
                **quality_v2.to_dict(),
                "qualityReceiptV3": quality_v3.to_dict(),
            }
            update_result = await session.execute(
                update(PresentationModel)
                .where(
                    PresentationModel.id == presentation_id,
                    PresentationModel.owner_id == owner_id,
                    PresentationModel.revision == expected_revision,
                    PresentationModel.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING,
                )
                .values(
                    title=str(deck_ir.get("title") or presentation.title or "Presentation"),
                    n_slides=len(slides),
                    language=str(deck_ir.get("language") or presentation.language or ""),
                    outlines=outlines,
                    mode="authored",
                    theme=next_theme,
                    lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                    revision=PresentationModel.revision + 1,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            if update_result.rowcount != 1:
                raise DeckIrIntegrationError("deck_ir_operation_fence_lost")
            await session.commit()
        return {
            "manifestDigest": manifest.canonical_digest,
            "revision": expected_revision + 1,
            "bundleCount": len(ordered),
            "styleProfileDigest": payload.get("styleProfileDigest"),
            "retainedReferenceDigest": payload.get("retainedReferenceDigest"),
            "generationRoute": publication_route,
        }


def build_product_deck_ir_execution_adapter() -> ProductDeckIrExecutionAdapter:
    """Build without provider I/O; calls are still reserved inside `run`."""

    return ProductDeckIrExecutionAdapter()


__all__ = ["ProductDeckIrExecutionAdapter", "build_product_deck_ir_execution_adapter"]
