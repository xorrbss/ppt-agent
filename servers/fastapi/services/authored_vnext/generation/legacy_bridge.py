"""Adapter from the existing authored presentation flow to authored-vNext."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from models.authored_vnext.deck import (
    AuthoredCompositionPin,
    DeckGenerationSnapshot,
    GenerateDeckRequest,
    GenerationResult,
    OutlineAssignment,
    OutlineAssignmentSet,
    OutlineInput,
    OutlineItem,
    PublicationFence,
    RegenerateBodyCommand,
    RegenerationResult,
)
from models.authored_vnext.master.operations import LegacyMasterApplyPin
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.presentation_outline_model import PresentationOutlineModel
from services.authored_checkpoint_service import (
    complete_stage,
    initialize_checkpoints,
    start_stage,
)
from services.authored_presentation_contracts import AuthoredGenerationPipelineResult
from services.authored_presentation_artifacts import _save_slide_pngs
from services.authored_vnext.generation.core_bridge import (
    CoreBridgeCheckpoint,
    DurableExportPlanBridge,
)
from services.authored_vnext.generation.ai_text_zones import (
    AITextZoneContext,
    AITextZoneResolutionRequest,
    ResolvedAITextZoneSet,
    materialize_ai_text_zone_contract,
    resolve_deck_ai_text_zones,
)
from services.authored_vnext.generation.effective_style import (
    materialize_authored_composition,
    resolve_authored_composition,
)
from services.authored_vnext.generation.pipeline import AuthoredVNextGenerationPipeline
from services.authored_vnext.generation.pipeline import (
    _published_checkpoint,
    _seal_deck,
    _validated_slide,
    _visual_plan,
)
from services.authored_vnext.generation.montage import build_generation_montage_manifest
from services.authored_vnext.generation.outline import (
    OutlineItem as PlanningOutlineItem,
    plan_outline,
)
from services.authored_vnext.generation.quality_wire import build_deck_quality_report
from services.authored_vnext.integration.artifact_store import BufferedArtifactStore
from services.authored_vnext.integration.export_service import (
    AuthoredVNextExportService,
    CreateJobCommand,
)
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
)
from services.authored_vnext.integration.worker import HttpOoxmlWorkerTransport
from services.authored_vnext.master.assets import OwnerMasterAssetStore
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.master.preview import build_preview
from utils.llm_calls.author_deck import AuthoredDeckResult
from utils.authored_styles import AuthoredStyle
from utils.slide_capture import render_html_to_png

from .legacy_pin import (
    LegacyMasterPinIdentity,
    ResolvedLegacyMasterPin,
    canonical_legacy_master_pin_identity,
    generation_repository_for_owner,
)


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_OUTLINE_VISUAL_DIRECTIVE = re.compile(
    r"^[-*•▪◦]\s*(?:카드(?:\s+구성)?|프로세스|타임라인|비교|메트릭|이미지|표|차트|긴\s+CJK)\s*패턴(?:\s+적용|으로|을|\?|$)",
    re.IGNORECASE,
)
_OUTLINE_SELF_COMMENTARY = re.compile(
    r"^[-*•▪◦]\s*(?:this\b|same\b|use\b|no\s+extras?\b|exact\b|比較\?|メトリック\?)",
    re.IGNORECASE,
)
_OUTLINE_SELF_COMMENTARY_TAIL = re.compile(
    r"\s+(?:lol\b|lmao\b|haha\b|wait\b|user\s+said\b|better\s+not\b|need\b|fix\b|다\s*됨ㅋ|哈哈).*$",
    re.IGNORECASE,
)
_EXPLICIT_SLIDE_SEQUENCE_ANCHOR = "다음 순서와 제목을 정확히 유지:"
_EXPLICIT_SLIDE_MARKER = re.compile(r"(?:^|,\s+)(\d{1,2})\s+")


@dataclass(frozen=True, slots=True)
class LegacyMasterGeneration:
    result: GenerationResult
    master: MasterRevisionResponse
    composition_pin: AuthoredCompositionPin
    ai_text_zones: ResolvedAITextZoneSet
    pipeline: AuthoredGenerationPipelineResult
    source_semantic_snapshot: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class LegacyNativeExport:
    path: Path
    artifact_sha256: str
    artifact_size_bytes: int
    source_snapshot_sha256: str
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class LegacyMasterApplyResult:
    deck: DeckGenerationSnapshot
    presentation_revision: int


@dataclass(frozen=True, slots=True)
class LegacyBodyRegenerationResult:
    result: RegenerationResult
    presentation_revision: int


class _PinnedMasterHeadRepository:
    """Keep pipeline validation on the revision frozen at design admission."""

    def __init__(self, repository, master: MasterRevisionResponse) -> None:
        self._repository = repository
        self._master = master

    def get_master_head(self, master_id: str) -> MasterRevisionResponse:
        if master_id == self._master.master_id:
            return self._master
        return self._repository.get_master_head(master_id)

    def __getattr__(self, name: str):
        return getattr(self._repository, name)


def _title_and_body(content: str, index: int) -> tuple[str, str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return f"Slide {index + 1}", "Content pending"
    title = _HEADING.sub("", lines[0]).strip(" -*") or f"Slide {index + 1}"
    body_lines = []
    for line in lines[1:]:
        if _OUTLINE_VISUAL_DIRECTIVE.match(line) is not None:
            continue
        if _OUTLINE_SELF_COMMENTARY.match(line) is not None:
            continue
        cleaned = _OUTLINE_SELF_COMMENTARY_TAIL.sub("", line).rstrip(" .;:,-")
        if cleaned:
            body_lines.append(cleaned)
    body = "\n".join(body_lines).strip() or lines[0]
    return title[:4000], body[:20000]


def _explicit_numbered_slide_contents(
    prompt: str, *, expected_count: int
) -> list[str] | None:
    anchor = prompt.find(_EXPLICIT_SLIDE_SEQUENCE_ANCHOR)
    if anchor < 0:
        return None
    sequence = prompt[anchor + len(_EXPLICIT_SLIDE_SEQUENCE_ANCHOR) :].strip()
    markers = list(_EXPLICIT_SLIDE_MARKER.finditer(sequence))
    if len(markers) != expected_count:
        return None
    if [int(match.group(1)) for match in markers] != list(
        range(1, expected_count + 1)
    ):
        return None
    contents: list[str] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(sequence)
        item = sequence[marker.end() : end].strip(" ,.")
        opening = item.find("(")
        closing = item.rfind(")")
        if opening <= 0 or closing <= opening:
            return None
        title = item[:opening].strip()
        body = item[opening + 1 : closing].strip()
        if not title or not body:
            return None
        contents.append(f"## {title}\n- {body}")
    return contents


def _generate_snapshot(
    *,
    owner_id: uuid.UUID,
    presentation_id: uuid.UUID,
    outline: PresentationOutlineModel,
    resolved: ResolvedLegacyMasterPin,
    authored_style: AuthoredStyle | None,
    composition_pin: AuthoredCompositionPin | None,
    ai_text_context: AITextZoneContext,
    source_semantic_snapshot: dict[str, object] | None = None,
) -> tuple[GenerationResult, MasterRevisionResponse, AuthoredCompositionPin]:
    repository = generation_repository_for_owner(owner_id)
    master = repository.get_master(
        resolved.pin.master_id, resolved.pin.master_revision
    )
    if composition_pin is None:
        composition_pin, _ = resolve_authored_composition(
            master.contract,
            style=authored_style,
            visual_direction=master.visual_direction,
        )
    else:
        # Admission has already sealed the exact body visual snapshot. Replay
        # it directly so mutable style YAML cannot change preview or export.
        materialize_authored_composition(master.contract, composition_pin)
    source_slides = _explicit_numbered_slide_contents(
        ai_text_context.prompt,
        expected_count=len(outline.slides),
    ) or [slide.content for slide in outline.slides]
    source_payload = {
        "presentationId": str(presentation_id),
        "slides": source_slides,
        "aiTextZoneContext": ai_text_context.model_dump(mode="json"),
        "compositionDigest": composition_pin.digest,
        "sourceSemanticSnapshotDigest": (
            source_semantic_snapshot.get("digest")
            if isinstance(source_semantic_snapshot, dict)
            else None
        ),
    }
    source_digest = canonical_digest(source_payload)
    items = []
    for index, content in enumerate(source_slides):
        title, body = _title_and_body(content, index)
        items.append(
            OutlineItem(
                item_id=f"outline:legacy-{index + 1}",
                index=index,
                title=title,
                summary=body,
            )
        )
    command = GenerateDeckRequest(
        operation_id=f"operation:legacy-{presentation_id.hex}",
        deck_id=f"deck:legacy-{presentation_id.hex}",
        master=resolved.pin,
        composition=composition_pin,
        outline=OutlineInput(
            source_digest=source_digest,
            deck_title=items[0].title,
            items=items,
        ),
        quality_policy_version="authored-vnext-quality/v1",
        publication_fence=PublicationFence(
            expected_deck_revision=0,
            expected_active_revision_digest=None,
        ),
    )
    row = repository.begin_operation(
        operation_id=command.operation_id,
        deck_id=command.deck_id,
        kind="deck.generate",
        idempotency_key=f"legacy-presentation:{presentation_id}",
        request=command,
        expected_revision=None,
        expected_digest=None,
    )
    if row.state == "succeeded" and row.result_payload is not None:
        result = GenerationResult.model_validate(row.result_payload)
        if result.composition != composition_pin:
            raise RuntimeError("authored_vnext_composition_changed")
        return result, master, composition_pin
    repository.claim_operation(
        command.operation_id,
        worker_id=f"legacy-api:{uuid.uuid4().hex}",
    )
    try:
        pipeline_repository = (
            _PinnedMasterHeadRepository(repository, master)
            if composition_pin is not None
            else repository
        )
        result = AuthoredVNextGenerationPipeline(
            pipeline_repository, DurableExportPlanBridge()
        ).generate(command)
    except Exception as error:
        repository.fail_operation(command.operation_id, error)
        raise
    return result, master, composition_pin


async def _checkpoint_generated_slides(
    *,
    generation_job,
    layout_ids: list[str],
    htmls: list[str],
    pngs: list[bytes],
) -> None:
    if generation_job is None:
        return
    attempt_token = generation_job.attempt_token
    if not attempt_token:
        raise RuntimeError("authored_attempt_lost")
    await initialize_checkpoints(
        job_id=generation_job.id,
        attempt_token=attempt_token,
        roles=layout_ids,
    )
    for index, (html, png) in enumerate(zip(htmls, pngs, strict=True)):
        await start_stage(
            job_id=generation_job.id,
            slide_index=index,
            stage="author",
            attempt_token=attempt_token,
        )
        await complete_stage(
            job_id=generation_job.id,
            slide_index=index,
            stage="author",
            attempt_token=attempt_token,
            html=html,
        )
        await start_stage(
            job_id=generation_job.id,
            slide_index=index,
            stage="illustrate",
            attempt_token=attempt_token,
        )
        await complete_stage(
            job_id=generation_job.id,
            slide_index=index,
            stage="illustrate",
            attempt_token=attempt_token,
            html=html,
            skipped=True,
        )
        await start_stage(
            job_id=generation_job.id,
            slide_index=index,
            stage="render",
            attempt_token=attempt_token,
        )
        await complete_stage(
            job_id=generation_job.id,
            slide_index=index,
            stage="render",
            attempt_token=attempt_token,
            png=png,
        )
        await start_stage(
            job_id=generation_job.id,
            slide_index=index,
            stage="persist",
            attempt_token=attempt_token,
        )
        await complete_stage(
            job_id=generation_job.id,
            slide_index=index,
            stage="persist",
            attempt_token=attempt_token,
        )


async def generate_legacy_master_owned_deck(
    *,
    owner_id: uuid.UUID,
    presentation_id: uuid.UUID,
    outline: PresentationOutlineModel,
    resolved: ResolvedLegacyMasterPin,
    authored_style: AuthoredStyle | None = None,
    composition_pin: AuthoredCompositionPin | None = None,
    deterministic_replay: bool = False,
    ai_text_context: AITextZoneContext | None = None,
    generation_job=None,
    source_semantic_snapshot: dict[str, object] | None = None,
) -> LegacyMasterGeneration:
    """Generate the legal vNext snapshot and derive the existing editor preview."""

    started = time.monotonic()
    context = ai_text_context or AITextZoneContext(
        materials=tuple(slide.content for slide in outline.slides)
    )
    result, master, composition_pin = await asyncio.to_thread(
        _generate_snapshot,
        owner_id=owner_id,
        presentation_id=presentation_id,
        outline=outline,
        resolved=resolved,
        authored_style=authored_style,
        composition_pin=composition_pin,
        ai_text_context=context,
        source_semantic_snapshot=source_semantic_snapshot,
    )
    repository = generation_repository_for_owner(owner_id)
    effective_contract = materialize_authored_composition(
        master.contract,
        composition_pin,
    )
    async def deterministic_zone_values(
        request: AITextZoneResolutionRequest,
    ) -> dict[str, str]:
        # Design replay may only rearrange the saved semantic manuscript. AI
        # furniture text therefore uses the master's declared fallback/hidden
        # behavior and never starts another model request.
        return {zone.element_id: "" for zone in request.zones}

    ai_text_zones = await resolve_deck_ai_text_zones(
        repository=repository,
        deck_id=result.deck.deck_id,
        master_digest=master.digest,
        contract=effective_contract,
        context=context,
        resolver=deterministic_zone_values if deterministic_replay else None,
        # The workspace operation already owns the publication transaction.
        # Deterministic design replay derives empty/fallback furniture text in
        # memory so a nested SQLite candidate write cannot deadlock it.
        use_repository_cache=not deterministic_replay,
    )
    materialized_master = master.model_copy(
        update={
            "contract": materialize_ai_text_zone_contract(
                effective_contract,
                ai_text_zones,
                expected_master_digest=master.digest,
            )
        }
    )
    htmls = [
        build_preview(
            materialized_master.contract,
            master.revision,
            item.slide,
            page_number=page_number,
        ).html
        for page_number, item in enumerate(result.deck.slides, start=1)
    ]
    render_started = time.monotonic()
    pngs = list(await asyncio.gather(*(render_html_to_png(html) for html in htmls)))
    render_work = time.monotonic() - render_started
    layout_ids = [item.slide.layout_id for item in result.deck.slides]
    await _checkpoint_generated_slides(
        generation_job=generation_job,
        layout_ids=layout_ids,
        htmls=htmls,
        pngs=pngs,
    )
    return LegacyMasterGeneration(
        result=result,
        master=materialized_master,
        composition_pin=composition_pin,
        ai_text_zones=ai_text_zones,
        pipeline=AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult(htmls=htmls, design_system="deckmaster-contract/v1"),
            pngs=pngs,
            elapsed=time.monotonic() - started,
            author_work=0.0,
            illustration_work=0.0,
            render_work=render_work,
        ),
        source_semantic_snapshot=source_semantic_snapshot,
    )


def _export_service(owner_id: uuid.UUID) -> AuthoredVNextExportService:
    owner_scope = str(owner_id)
    app_data = Path(os.getenv("APP_DATA_DIRECTORY", "app_data"))
    repository = AuthoredVNextSqlRepository.from_application_database(
        owner_scope=owner_scope,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        owner_id=owner_id,
    )
    return AuthoredVNextExportService(
        repository,
        HttpOoxmlWorkerTransport.from_environment(),
        BufferedArtifactStore(
            app_data / "exports" / "users" / owner_scope / "authored-vnext"
        ),
        OwnerMasterAssetStore(
            app_data
            / "authored_vnext"
            / "master-assets"
            / "users"
            / owner_scope
        ),
    )


def _asset_digests(contract) -> list[str]:
    return sorted(
        {
            asset.digest
            for asset in (contract.brand_assets.logo, contract.brand_assets.wordmark)
            if asset is not None
        }
    )


def build_legacy_generation_export_payload(
    generation: LegacyMasterGeneration,
) -> dict[str, object]:
    """One immutable preview/native-export envelope, exposed for verification."""

    deck = generation.result.deck
    if deck.scene is None:
        # Old image-authored documents remain readable through their frozen
        # compatibility route, but cannot enter the new canonical export job.
        raise RuntimeError("authored_vnext_scene_required")
    return {
        "source": "legacy-presentation-generate",
        "deckId": deck.deck_id,
        "deckRevision": deck.revision,
        "deckDigest": deck.digest,
        "sceneDigest": deck.scene.scene_digest,
        "scene": deck.scene.model_dump(mode="json", by_alias=True),
        "assetManifest": [
            item.model_dump(mode="json", by_alias=True)
            for item in deck.scene.asset_manifest
        ],
        "aiTextZones": generation.ai_text_zones.model_dump(mode="json"),
        "compositionPin": generation.composition_pin.model_dump(
            mode="json", by_alias=True
        ),
        **(
            {"sourceSemanticSnapshot": generation.source_semantic_snapshot}
            if generation.source_semantic_snapshot is not None
            else {}
        ),
    }


def _legacy_export_job_identity(
    deck_id: str,
    deck_digest: str,
    *,
    execution_scope: str | None,
) -> str:
    """Scope an export intent to one durable generation attempt.

    A failed worker transition can leave its export job leased/running. A
    later workspace retry must not reclaim that stale job, but repeat calls
    inside the same generation attempt must remain idempotent.
    """

    base = f"legacy-generation:{deck_id}:{deck_digest}"
    if execution_scope is None:
        return base
    if not execution_scope:
        raise ValueError("legacy export execution scope must not be empty")
    scope_digest = sha256(execution_scope.encode("utf-8")).hexdigest()
    return f"{base}:attempt:{scope_digest}"


def _verified_resolved_snapshot_evidence(
    generation: LegacyMasterGeneration,
    *,
    readback_verified: bool,
) -> dict[str, object]:
    """Build UI evidence only after the native worker passed strict readback."""

    deck = generation.result.deck
    if deck.scene is None:
        raise RuntimeError("authored_vnext_scene_required")
    composition = deck.composition
    template = composition.authored_template if composition is not None else None
    policy = composition.consistency_policy if composition is not None else None
    resolved = template.resolved_reference_layout if template is not None else None
    if (
        readback_verified is not True
        or composition is None
        or template is None
        or policy is None
        or resolved is None
        or template.resolved_reference_layout_digest != resolved.digest
    ):
        raise RuntimeError("authored_vnext_visual_evidence_incomplete")

    scene = deck.scene
    if scene is None or len(scene.slides) != len(deck.slides):
        raise RuntimeError("authored_vnext_visual_evidence_incomplete")
    page_numbers = list(range(1, len(scene.slides) + 1))
    semantic_roles = list(
        dict.fromkeys(
            element.semantic_role
            for slide in scene.slides
            for element in slide.elements
        )
    )
    layout_roles = list(
        dict.fromkeys(item.slide.layout_id for item in deck.slides)
    )
    slide_count = len(deck.slides)
    if page_numbers != list(range(1, slide_count + 1)):
        raise RuntimeError("authored_vnext_visual_evidence_incomplete")
    if not semantic_roles or not layout_roles:
        raise RuntimeError("authored_vnext_visual_evidence_incomplete")

    template_snapshot = {
        "resolved_template_id": template.retained_reference_id,
        "resolved_template_digest": template.digest,
        "reference_layout_digest": resolved.digest,
        "retained_reference_digest": resolved.source_digest,
    }
    snapshot = {
        **template_snapshot,
        "composition_digest": composition.digest,
        "rule_digest": policy.digest,
        "slide_count": slide_count,
    }
    return {
        "resolved_template_id": template.retained_reference_id,
        "resolved_template_digest": template.digest,
        "resolved_reference_layout_digest": resolved.digest,
        "resolved_composition_digest": composition.digest,
        "resolved_composition_pin": composition.model_dump(
            mode="json", by_alias=True
        ),
        "resolved_rule_digest": policy.digest,
        "template_snapshot": template_snapshot,
        "semantic_roles_used": semantic_roles,
        "layout_roles_used": layout_roles,
        "page_numbers": page_numbers,
        "slide_count": slide_count,
        "preview_snapshot": dict(snapshot),
        "native_pptx_snapshot": dict(snapshot),
        "readback_verified": True,
        "scene_digest": deck.scene.scene_digest,
        "scene_slide_digests": [
            slide.slide_digest for slide in deck.scene.slides
        ],
        "scene_composition_digest": deck.scene.composition_digest,
        "visual_fidelity": {
            "status": "passed",
            "basis": "resolved-geometry-and-native-readback",
            "reference_layout_count": len(resolved.layouts),
        },
    }


async def export_legacy_master_owned_deck(
    *,
    owner_id: uuid.UUID,
    generation: LegacyMasterGeneration,
    destination: str,
    reset_failed_job_attempts: bool = False,
    execution_scope: str | None = None,
    operation_fence: int = 1,
) -> tuple[str, dict[str, object]]:
    """Run the native OOXML job and copy its verified LKG to the legacy path."""

    service = _export_service(owner_id)
    deck = generation.result.deck
    active = await asyncio.to_thread(service.repository.get_active_artifact, deck.deck_id)
    if active is None:
        payload = build_legacy_generation_export_payload(generation)
        job = await asyncio.to_thread(
            service.create_job,
            CreateJobCommand(
                deck_id=deck.deck_id,
                expected_deck_revision=deck.revision,
                expected_deck_digest=deck.digest,
                expected_master_revision=deck.master.master_revision,
                expected_master_digest=deck.master.master_digest,
                expected_active_artifact_revision=0,
                expected_active_artifact_digest=None,
                expected_source_package_digest=None,
                expected_asset_digests=_asset_digests(generation.master.contract),
                expected_font_digest=deck.master.font_manifest_digest,
                idempotency_key=_legacy_export_job_identity(
                    deck.deck_id,
                    deck.digest,
                    execution_scope=execution_scope,
                ),
                timeout_ms=120_000,
                max_attempts=3,
                request_payload=payload,
                operation_fence=operation_fence,
            ),
        )
        if job.state in {"failed", "cancelled"} and (
            job.attempt_count < job.max_attempts or reset_failed_job_attempts
        ):
            job = await asyncio.to_thread(
                service.repository.retry_export_job,
                job.id,
                expected_operation_fence=job.operation_fence,
                reset_attempts=(
                    reset_failed_job_attempts
                    and job.attempt_count >= job.max_attempts
                ),
            )
        completed = await service.run_job(job.id)
        if completed.state != "succeeded":
            raise RuntimeError(
                completed.failure_code or "authored_vnext_native_export_not_published"
            )
        active = await asyncio.to_thread(
            service.repository.get_active_artifact, deck.deck_id
        )
    if active is None or active[1].deck_revision != deck.revision:
        raise RuntimeError("authored_vnext_native_export_not_published")
    receipt = active[1]
    source = await asyncio.to_thread(
        service.artifact_store.open_verified,
        receipt.storage_key,
        receipt.artifact_digest,
        receipt.size_bytes,
    )
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.native"
    try:
        await asyncio.to_thread(shutil.copyfile, source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    evidence = _verified_resolved_snapshot_evidence(
        generation,
        readback_verified=receipt.readback_verified,
    )
    return str(target), evidence


async def export_persisted_legacy_master_deck(
    *,
    owner_id: uuid.UUID,
    projection: dict[str, object],
) -> LegacyNativeExport:
    """Export a persisted projection only after every vNext CAS field matches."""

    deck_id = projection.get("deckId")
    deck_revision = projection.get("deckRevision")
    deck_digest = projection.get("deckDigest")
    source_digest = projection.get("sourceDigest")
    scene_digest = projection.get("sceneDigest")
    scene_payload = projection.get("scene")
    pin_payload = projection.get("masterPin")
    ai_text_zones = projection.get("aiTextZones")
    composition_pin = projection.get("compositionPin")
    source_semantic_snapshot = projection.get("sourceSemanticSnapshot")
    projection_version = projection.get("schemaVersion")
    if (
        projection_version
        not in {
            "legacy-authored-vnext-projection/v1",
            "authored-vnext-scene-projection/v1",
        }
        or not isinstance(deck_id, str)
        or not isinstance(deck_revision, int)
        or not isinstance(deck_digest, str)
        or not isinstance(source_digest, str)
        or not isinstance(pin_payload, dict)
        or (
            projection_version == "authored-vnext-scene-projection/v1"
            and (not isinstance(scene_digest, str) or not isinstance(scene_payload, dict))
        )
    ):
        raise RuntimeError("authored_vnext_export_projection_incomplete")
    service = _export_service(owner_id)
    deck = await asyncio.to_thread(service.repository.get_deck, deck_id)
    persisted_composition = (
        deck.composition.model_dump(mode="json", by_alias=True)
        if deck.composition is not None
        else None
    )
    persisted_scene = (
        deck.scene.model_dump(mode="json", by_alias=True)
        if deck.scene is not None
        else None
    )
    if (
        deck.revision != deck_revision
        or deck.digest != deck_digest
        or deck.source_digest != source_digest
        or deck.master.model_dump(mode="json", by_alias=True) != pin_payload
        or persisted_composition != composition_pin
        or (deck.scene.scene_digest if deck.scene is not None else None) != scene_digest
        or persisted_scene != scene_payload
    ):
        raise RuntimeError("authored_vnext_export_projection_stale")
    if isinstance(source_semantic_snapshot, dict):
        if (
            source_semantic_snapshot.get("digest")
            != projection.get("sourceSemanticSnapshotDigest")
            or source_semantic_snapshot.get("slideCount") != len(deck.slides)
        ):
            raise RuntimeError("authored_vnext_source_semantic_snapshot_stale")
    master = await asyncio.to_thread(
        service.repository.get_master,
        deck.master.master_id,
        deck.master.master_revision,
    )
    if master.status != "published" or master.digest != deck.master.master_digest:
        raise RuntimeError("authored_vnext_published_master_required")
    active = await asyncio.to_thread(service.repository.get_active_artifact, deck_id)
    if active is None or active[1].deck_revision != deck.revision:
        expected_revision = active[0].revision if active is not None else 0
        expected_digest = active[0].digest if active is not None else None
        job = await asyncio.to_thread(
            service.create_job,
            CreateJobCommand(
                deck_id=deck.deck_id,
                expected_deck_revision=deck.revision,
                expected_deck_digest=deck.digest,
                expected_master_revision=deck.master.master_revision,
                expected_master_digest=deck.master.master_digest,
                expected_active_artifact_revision=expected_revision,
                expected_active_artifact_digest=expected_digest,
                expected_source_package_digest=None,
                expected_asset_digests=_asset_digests(master.contract),
                expected_font_digest=deck.master.font_manifest_digest,
                idempotency_key=f"workspace-export:{deck.deck_id}:{deck.digest}",
                timeout_ms=120_000,
                max_attempts=3,
                request_payload={
                    "source": "workspace-export",
                    "deckId": deck.deck_id,
                    "deckRevision": deck.revision,
                    "deckDigest": deck.digest,
                    **(
                        {
                            "sceneDigest": deck.scene.scene_digest,
                            "scene": deck.scene.model_dump(
                                mode="json", by_alias=True
                            ),
                            "assetManifest": [
                                item.model_dump(mode="json", by_alias=True)
                                for item in deck.scene.asset_manifest
                            ],
                        }
                        if deck.scene is not None
                        else {}
                    ),
                    **(
                        {"aiTextZones": ai_text_zones}
                        if isinstance(ai_text_zones, dict)
                        else {}
                    ),
                    **(
                        {"compositionPin": composition_pin}
                        if isinstance(composition_pin, dict)
                        else {}
                    ),
                    **(
                        {"sourceSemanticSnapshot": source_semantic_snapshot}
                        if isinstance(source_semantic_snapshot, dict)
                        else {}
                    ),
                },
            ),
        )
        await service.run_job(job.id)
        active = await asyncio.to_thread(service.repository.get_active_artifact, deck_id)
    if active is None or active[1].deck_revision != deck.revision:
        raise RuntimeError("authored_vnext_native_export_not_published")
    pointer, receipt = active
    path = await asyncio.to_thread(
        service.artifact_store.open_verified,
        receipt.storage_key,
        receipt.artifact_digest,
        receipt.size_bytes,
    )
    return LegacyNativeExport(
        path=path,
        artifact_sha256=receipt.artifact_digest.removeprefix("sha256:"),
        artifact_size_bytes=receipt.size_bytes,
        source_snapshot_sha256=deck.digest.removeprefix("sha256:"),
        provenance={
            "schema_version": 1,
            "adapter_chain": ["native_reconstructed"],
            "capability_evidence": {
                "authored_vnext_native_master": True,
                "deck_revision": deck.revision,
                "deck_digest": deck.digest,
                "master_digest": deck.master.master_digest,
                "artifact_publication_revision": pointer.revision,
                "package_digest": receipt.package_digest,
                "read_back_verified": True,
            },
            "fallback_reason": None,
            "asset_digests": {},
            "compiler_runtime": {
                "compiler": "OoxmlMasterCompilerPort",
                "compiler_version": "ooxml-master-port/v1",
                "runtime": "authored-vnext-ooxml-worker",
                "runtime_version": "1",
            },
            "freshness": {
                "source_revision": deck.revision,
                "source_snapshot_sha256": deck.digest.removeprefix("sha256:"),
                "package_sha256": receipt.package_digest.removeprefix("sha256:"),
                "asset_digests_match": True,
                "package_digest_match": True,
            },
        },
    )


def _prepare_remaster(
    *,
    repository,
    presentation_id: uuid.UUID,
    expected_presentation_revision: int,
    expected_deck_revision: int,
    expected_deck_digest: str,
    resolved: ResolvedLegacyMasterPin,
    idempotency_key: str,
):
    current = repository.get_deck(f"deck:legacy-{presentation_id.hex}")
    if (
        current.revision != expected_deck_revision
        or current.digest != expected_deck_digest
    ):
        raise RuntimeError("authored_vnext_generation_conflict")
    old_master = repository.get_master(
        current.master.master_id, current.master.master_revision
    )
    new_master = repository.get_master(
        resolved.pin.master_id, resolved.pin.master_revision
    )
    operation_id = f"operation:master-apply-{presentation_id.hex}-{expected_presentation_revision}"
    outlines = []
    planning = []
    for index, item in enumerate(current.slides):
        slide = item.slide
        role_hint = (
            "section"
            if slide.layout_id == old_master.contract.layouts.section.id
            else None
        )
        outlines.append(
            OutlineItem(
                item_id=f"outline:legacy-{index + 1}",
                index=index,
                title=slide.title.text,
                summary=slide.body,
                role_hint=role_hint,
            )
        )
        planning.append(
            PlanningOutlineItem(
                slide_id=slide.slide_id,
                title=slide.title.text,
                body=slide.body,
                kind="section" if role_hint == "section" else "content",
            )
        )
    command = GenerateDeckRequest(
        operation_id=operation_id,
        deck_id=current.deck_id,
        master=resolved.pin,
        outline=OutlineInput(
            source_digest=current.source_digest,
            deck_title=outlines[0].title,
            items=outlines,
        ),
        quality_policy_version="authored-vnext-quality/v1",
        publication_fence=PublicationFence(
            expected_deck_revision=current.revision,
            expected_active_revision_digest=current.digest,
        ),
    )
    row = repository.begin_operation(
        operation_id=operation_id,
        deck_id=current.deck_id,
        kind="deck.master.apply",
        idempotency_key=idempotency_key,
        request=command,
        expected_revision=current.revision,
        expected_digest=current.digest,
    )
    if row.state == "succeeded" and row.result_payload is not None:
        return GenerationResult.model_validate(row.result_payload), new_master, True
    repository.claim_operation(operation_id, worker_id=f"legacy-apply:{uuid.uuid4().hex}")
    planned = plan_outline(new_master, planning)
    validated = []
    plans = []
    assignments = []
    for index, (outline_item, planned_slide, prior) in enumerate(
        zip(outlines, planned.slides, current.slides, strict=True)
    ):
        plan, sealed = _validated_slide(
            new_master,
            planned_slide,
            outline_item.item_id,
            current.source_digest,
            resolved.pin,
            None,
            command.quality_policy_version,
            prior.slide.revision + 1,
        )
        plans.append(plan)
        validated.append(sealed)
        role = "longTitle" if planned_slide.role == "long-title" else planned_slide.role
        assignments.append(
            OutlineAssignment(
                item_id=outline_item.item_id,
                index=index,
                role=role,
                layout_id=planned_slide.layout_id,
                title_promoted=role == "longTitle",
                reason=(
                    "first-slide-cover"
                    if index == 0
                    else "title-fit-promotion"
                    if role == "longTitle"
                    else "section-boundary"
                    if role == "section"
                    else "standard-content"
                ),
            )
        )
    deck = _seal_deck(
        deck_id=current.deck_id,
        revision=current.revision + 1,
        parent=current.digest,
        pin=resolved.pin,
        composition=None,
        source_digest=current.source_digest,
        slides=validated,
    )
    quality = build_deck_quality_report(new_master.contract, deck, plans)
    if not quality.passed:
        raise RuntimeError("authored_vnext_candidate_rejected")
    if deck.scene is None:
        raise RuntimeError("authored_vnext_scene_required")
    artifact = DurableExportPlanBridge().export(
        master=new_master.contract,
        master_revision=new_master.revision,
        slides=[item.slide for item in validated],
        checkpoint=CoreBridgeCheckpoint(
            master_digest=resolved.pin.master_digest,
            layout_registry_digest=resolved.pin.layout_registry_digest,
            font_manifest_digest=resolved.pin.font_manifest_digest,
            source_digest=current.source_digest,
            scene_digest=deck.scene.scene_digest,
        ),
        scene=deck.scene,
    )
    result = GenerationResult(
        operation_id=operation_id,
        checkpoint=_published_checkpoint(
            command,
            validated,
            scene_digest=deck.scene.scene_digest,
        ),
        outline_assignments=OutlineAssignmentSet(
            source_digest=current.source_digest,
            master_digest=resolved.pin.master_digest,
            layout_registry_digest=resolved.pin.layout_registry_digest,
            assignments=assignments,
        ),
        deck=deck,
        quality=quality,
        montage=build_generation_montage_manifest(deck),
        export_artifact_digest=artifact.artifact_digest,
    )
    return result, new_master, False


async def apply_master_to_legacy_presentation(
    *,
    presentation_id: uuid.UUID,
    expected_presentation_revision: int,
    expected_deck_revision: int,
    expected_deck_digest: str,
    requested_pin: LegacyMasterApplyPin,
    idempotency_key: str,
    resolved: ResolvedLegacyMasterPin,
) -> LegacyMasterApplyResult:
    repository = generation_repository_for_owner(resolved.owner_id)
    result, master, already_published = await asyncio.to_thread(
        _prepare_remaster,
        repository=repository,
        presentation_id=presentation_id,
        expected_presentation_revision=expected_presentation_revision,
        expected_deck_revision=expected_deck_revision,
        expected_deck_digest=expected_deck_digest,
        resolved=resolved,
        idempotency_key=idempotency_key,
    )
    if already_published:
        return LegacyMasterApplyResult(
            deck=result.deck,
            presentation_revision=expected_presentation_revision + 1,
        )
    htmls = [
        build_preview(
            master.contract,
            master.revision,
            item.slide,
            page_number=page_number,
        ).html
        for page_number, item in enumerate(result.deck.slides, start=1)
    ]
    pngs = list(await asyncio.gather(*(render_html_to_png(html) for html in htmls)))
    image_refs = await asyncio.to_thread(
        _save_slide_pngs,
        presentation_id,
        pngs,
        f"master-apply:{result.deck.digest}",
    )
    projection = {
        "schemaVersion": "legacy-authored-vnext-projection/v1",
        "deckId": result.deck.deck_id,
        "deckRevision": result.deck.revision,
        "deckDigest": result.deck.digest,
        "sceneDigest": (
            result.deck.scene.scene_digest if result.deck.scene is not None else None
        ),
        "scene": (
            result.deck.scene.model_dump(mode="json", by_alias=True)
            if result.deck.scene is not None
            else None
        ),
        "sourceDigest": result.deck.source_digest,
        "masterPin": result.deck.master.model_dump(mode="json", by_alias=True),
        "exportArtifactDigest": result.export_artifact_digest,
    }
    slide_updates = {}
    for index, item in enumerate(result.deck.slides):
        pin_payload = result.deck.master.model_dump(mode="json", by_alias=True)
        slide_updates[index] = {
                "layout": f"authored:{item.slide.layout_id}",
                "layout_group": "authored",
                "html_content": htmls[index],
                "content": {
                    "__authored__": True,
                    "image": image_refs[index],
                    "role": item.slide.layout_id,
                    "masterOwned": True,
                    "layoutId": item.slide.layout_id,
                    "masterPin": pin_payload,
                    "slideSpecDigest": item.slide.spec_digest,
                    "sceneSlideDigest": (
                        item.scene.slide_digest if item.scene is not None else None
                    ),
                    "sceneSlide": (
                        item.scene.model_dump(mode="json", by_alias=True)
                        if item.scene is not None
                        else None
                    ),
                },
                "properties": {"image": image_refs[index]},
            }
    try:
        await asyncio.to_thread(
            repository.publish_legacy_master_apply,
            result.deck,
            result,
            presentation_id=presentation_id,
            expected_presentation_revision=expected_presentation_revision,
            expected_revision=expected_deck_revision,
            expected_digest=expected_deck_digest,
            authored_projection=projection,
            slide_updates=slide_updates,
        )
    except Exception as error:
        repository.fail_operation(result.operation_id, error)
        raise
    return LegacyMasterApplyResult(
        deck=result.deck,
        presentation_revision=expected_presentation_revision + 1,
    )


def _prepare_body_regeneration(
    *,
    repository,
    operation_id: str,
    idempotency_key: str,
    deck_id: str,
    expected_deck_revision: int,
    expected_deck_digest: str,
    expected_master_pin: LegacyMasterPinIdentity,
    slide_index: int,
    layout_id: str,
    body: str,
):
    deck = repository.get_deck(deck_id)
    if (
        deck.revision != expected_deck_revision
        or deck.digest != expected_deck_digest
        or slide_index < 0
        or slide_index >= len(deck.slides)
    ):
        raise RuntimeError("authored_vnext_generation_conflict")
    if (
        canonical_legacy_master_pin_identity(
            deck.master.model_dump(mode="json", by_alias=True)
        )
        != expected_master_pin
    ):
        raise RuntimeError("authored_vnext_regenerate_master_changed")
    current = deck.slides[slide_index]
    if current.slide.layout_id != layout_id:
        raise RuntimeError("authored_vnext_regenerate_layout_changed")
    master = repository.get_master(
        deck.master.master_id, deck.master.master_revision
    )
    command = RegenerateBodyCommand(
        operation_id=operation_id,
        deck_id=deck.deck_id,
        slide_id=current.slide.slide_id,
        expected_slide_revision=current.slide.revision,
        expected_slide_spec_digest=current.slide.spec_digest,
        master=deck.master,
        source_digest=deck.source_digest,
        body=body,
        publication_fence=PublicationFence(
            expected_deck_revision=deck.revision,
            expected_active_revision_digest=deck.digest,
        ),
    )
    row = repository.begin_operation(
        operation_id=operation_id,
        deck_id=deck.deck_id,
        kind="slide.regenerate.publish",
        idempotency_key=idempotency_key,
        request=command,
        expected_revision=deck.revision,
        expected_digest=deck.digest,
    )
    if row.state == "succeeded" and row.result_payload is not None:
        return RegenerationResult.model_validate(row.result_payload), master, True
    repository.claim_operation(operation_id, worker_id=f"legacy-regenerate:{uuid.uuid4().hex}")
    pipeline = AuthoredVNextGenerationPipeline(repository, DurableExportPlanBridge())
    candidate = pipeline.prepare_regeneration(command)
    slides = list(deck.slides)
    slides[slide_index] = candidate.slide
    published = _seal_deck(
        deck_id=deck.deck_id,
        revision=deck.revision + 1,
        parent=deck.digest,
        pin=deck.master,
        composition=deck.composition,
        source_digest=deck.source_digest,
        slides=slides,
    )
    plans = []
    for index, item in enumerate(slides):
        if index == slide_index:
            plans.append(candidate.visual_plan)
            continue
        bounds = next(
            slot.bounds
            for layout in (
                master.contract.layouts.cover,
                master.contract.layouts.section,
                master.contract.layouts.standard,
                master.contract.layouts.long_title,
            )
            for slot in layout.slots
            if slot.id == item.checkpoint.body_slot_id
        )
        plans.append(
            _visual_plan(
                slide_id=item.slide.slide_id,
                item_id=f"outline:{item.slide.slide_id.removeprefix('slide:')}",
                role=(
                    "longTitle"
                    if item.slide.layout_id == master.contract.layouts.long_title.id
                    else "standard"
                ),
                layout_id=item.slide.layout_id,
                slot_id=item.checkpoint.body_slot_id,
                source_digest=item.checkpoint.source_digest,
                bounds=bounds,
                body=item.slide.body,
            )
        )
    effective_contract = (
        materialize_authored_composition(master.contract, deck.composition)
        if deck.composition is not None
        else master.contract
    )
    quality = build_deck_quality_report(effective_contract, published, plans)
    if not quality.passed:
        raise RuntimeError("authored_vnext_candidate_rejected")
    if published.scene is None:
        raise RuntimeError("authored_vnext_scene_required")
    DurableExportPlanBridge().export(
        master=effective_contract,
        master_revision=master.revision,
        slides=[item.slide for item in slides],
        checkpoint=CoreBridgeCheckpoint(
            master_digest=deck.master.master_digest,
            layout_registry_digest=deck.master.layout_registry_digest,
            font_manifest_digest=deck.master.font_manifest_digest,
            source_digest=deck.source_digest,
            composition=deck.composition,
            scene_digest=published.scene.scene_digest,
        ),
        scene=published.scene,
    )
    result = RegenerationResult(
        operation_id=operation_id,
        candidate=candidate,
        deck=published,
        quality=quality,
    )
    return result, master, False


async def regenerate_legacy_master_body(
    *,
    owner_id: uuid.UUID,
    presentation_id: uuid.UUID,
    expected_presentation_revision: int,
    projection: dict[str, object],
    slide_index: int,
    layout_id: str,
    body: str,
    operation_uuid: uuid.UUID,
) -> LegacyBodyRegenerationResult:
    repository = generation_repository_for_owner(owner_id)
    deck_id = projection.get("deckId")
    deck_revision = projection.get("deckRevision")
    deck_digest = projection.get("deckDigest")
    expected_master_pin = canonical_legacy_master_pin_identity(
        projection.get("masterPin")
    )
    if (
        not isinstance(deck_id, str)
        or not isinstance(deck_revision, int)
        or not isinstance(deck_digest, str)
        or expected_master_pin is None
    ):
        raise RuntimeError("authored_vnext_export_projection_incomplete")
    operation_id = f"operation:workspace-{operation_uuid.hex}"
    result, master, already_published = await asyncio.to_thread(
        _prepare_body_regeneration,
        repository=repository,
        operation_id=operation_id,
        idempotency_key=f"workspace-regenerate:{operation_uuid}",
        deck_id=deck_id,
        expected_deck_revision=deck_revision,
        expected_deck_digest=deck_digest,
        expected_master_pin=expected_master_pin,
        slide_index=slide_index,
        layout_id=layout_id,
        body=body,
    )
    if already_published:
        return LegacyBodyRegenerationResult(
            result=result,
            presentation_revision=expected_presentation_revision + 1,
        )
    item = result.deck.slides[slide_index]
    preview_contract = (
        materialize_authored_composition(master.contract, result.deck.composition)
        if result.deck.composition is not None
        else master.contract
    )
    html = build_preview(
        preview_contract,
        master.revision,
        item.slide,
        page_number=slide_index + 1,
    ).html
    png = await render_html_to_png(html)
    image_ref = (
        await asyncio.to_thread(
            _save_slide_pngs,
            presentation_id,
            [png],
            f"body-regenerate:{result.deck.digest}:{slide_index}",
        )
    )[0]
    projection = {
        **projection,
        "deckRevision": result.deck.revision,
        "deckDigest": result.deck.digest,
        "sceneDigest": (
            result.deck.scene.scene_digest if result.deck.scene is not None else None
        ),
        "scene": (
            result.deck.scene.model_dump(mode="json", by_alias=True)
            if result.deck.scene is not None
            else None
        ),
    }
    pin_payload = result.deck.master.model_dump(mode="json", by_alias=True)
    slide_update = {
        slide_index: {
            "layout": f"authored:{item.slide.layout_id}",
            "layout_group": "authored",
            "html_content": html,
            "content": {
                "__authored__": True,
                "image": image_ref,
                "role": item.slide.layout_id,
                "masterOwned": True,
                "layoutId": item.slide.layout_id,
                "masterPin": pin_payload,
                "slideSpecDigest": item.slide.spec_digest,
                "sceneSlideDigest": (
                    item.scene.slide_digest if item.scene is not None else None
                ),
                "sceneSlide": (
                    item.scene.model_dump(mode="json", by_alias=True)
                    if item.scene is not None
                    else None
                ),
            },
            "properties": {"image": image_ref},
        }
    }
    try:
        await asyncio.to_thread(
            repository.publish_legacy_master_apply,
            result.deck,
            result,
            presentation_id=presentation_id,
            expected_presentation_revision=expected_presentation_revision,
            expected_revision=deck_revision,
            expected_digest=deck_digest,
            authored_projection=projection,
            slide_updates=slide_update,
        )
    except Exception as error:
        repository.fail_operation(result.operation_id, error)
        raise
    return LegacyBodyRegenerationResult(
        result=result,
        presentation_revision=expected_presentation_revision + 1,
    )


__all__ = [
    "LegacyMasterGeneration",
    "LegacyNativeExport",
    "LegacyMasterApplyResult",
    "LegacyBodyRegenerationResult",
    "apply_master_to_legacy_presentation",
    "export_legacy_master_owned_deck",
    "export_persisted_legacy_master_deck",
    "generate_legacy_master_owned_deck",
    "regenerate_legacy_master_body",
]
