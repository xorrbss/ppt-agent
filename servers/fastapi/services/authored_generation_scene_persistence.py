"""Extracted implementation behind the authored generation facade."""

from __future__ import annotations

import logging


LOGGER = logging.getLogger(__name__)


def _semantic_deck_difference_paths(
    stored: object,
    candidate: object,
    *,
    path: str = "$",
    limit: int = 20,
) -> list[str]:
    """Return bounded structural paths without exposing scene payload values."""

    if type(stored) is not type(candidate):
        return [path]
    if isinstance(stored, dict):
        paths: list[str] = []
        for key in sorted(set(stored) | set(candidate), key=str):
            if len(paths) >= limit:
                break
            key_path = f"{path}.{key}"
            if key not in stored or key not in candidate:
                paths.append(key_path)
                continue
            paths.extend(
                _semantic_deck_difference_paths(
                    stored[key], candidate[key], path=key_path,
                    limit=limit - len(paths),
                )
            )
        return paths[:limit]
    if isinstance(stored, list):
        paths = []
        if len(stored) != len(candidate):
            paths.append(f"{path}.length")
        for index, (left, right) in enumerate(zip(stored, candidate, strict=False)):
            if len(paths) >= limit:
                break
            paths.extend(
                _semantic_deck_difference_paths(
                    left, right, path=f"{path}[{index}]", limit=limit - len(paths)
                )
            )
        return paths[:limit]
    return [] if stored == candidate else [path]

def _persist_or_reuse_identical_semantic_deck(
    repository,
    deck: DeckGenerationSnapshot,
) -> DeckSceneIR:
    """Recover only the same immutable import; never advance an existing head."""

    try:
        current = repository.get_deck(deck.deck_id)
    except GenerationNotFoundError:
        try:
            repository.append_imported_deck(deck)
        except DeckAlreadyExistsError:
            # A concurrent importer may have won after the read. Inspect its
            # current head with the same exact predicate as restart recovery.
            pass
        current = repository.get_deck(deck.deck_id)

    stored_payload = current.model_dump(mode="json", by_alias=True)
    stored_digest = stored_payload.pop("digest")
    if stored_digest != canonical_digest(stored_payload):
        raise DeckAlreadyExistsError()
    candidate_payload = deck.model_dump(
        mode="json", by_alias=True, exclude={"digest"}
    )
    # _seal_deck and slide checkpoints include wall-clock timestamps. All other
    # payload fields (including the complete Scene, assets and checkpoint
    # digests) must still match; a new source, revision or geometry is not a retry.
    stored_payload.pop("createdAt")
    candidate_payload.pop("createdAt")
    for payload in (stored_payload, candidate_payload):
        for item in payload.get("slides", []):
            checkpoint = item.get("checkpoint")
            if isinstance(checkpoint, dict):
                checkpoint.pop("validatedAt", None)
    if current.scene is None or stored_payload != candidate_payload:
        paths = _semantic_deck_difference_paths(stored_payload, candidate_payload)
        LOGGER.warning(
            "authored semantic deck retry mismatch deck=%s paths=%s count=%d "
            "source_digest_matches=%s",
            deck.deck_id,
            paths,
            len(paths),
            stored_payload.get("sourceDigest") == candidate_payload.get("sourceDigest"),
        )
        raise DeckAlreadyExistsError()
    return current.scene


def _append_staging_retry_semantic_deck(repository, deck, recovery_context):
    """Append a separately named, fenced pre-publication recovery candidate."""

    return repository.append_staging_retry_imported_deck(
        deck, recovery_context=recovery_context
    )

async def _persist_master_pinned_editable_scene_deck(
    *,
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    outline: PresentationOutlineModel,
    owner_id: uuid.UUID,
    style,
    htmls: list[str],
    pngs: list[bytes],
    semantic_components: list[dict[str, object]],
    scene_envelope: AuthoredDeckSceneEnvelope,
    revision: int,
    browser_metrics_by_slide=None,
    recovery_context=None,
    recovery_deck_id: str | None = None,
):
    """Bridge normal Authored output into the vNext durable CAS deck log.

    Authored HTML plus browser telemetry is the sole visual/semantic authority.
    The pinned master contributes provenance and compatibility metadata only;
    raw outline text is never compiled into a second, competing Scene.
    """

    if tuple(htmls) != scene_envelope.html_projections:
        raise ValueError("authored_scene_envelope_publication_projection_mismatch")
    if tuple(semantic_components) != scene_envelope.semantic_components:
        raise ValueError("authored_scene_envelope_publication_semantics_mismatch")
    # Async boundary: nothing below may touch the synchronous CAS on the loop.
    repository = async_generation_repository(generation_repository_for_owner(owner_id))
    resolved = request._authored_master_resolution
    composition_pin = request._authored_composition_pin
    if resolved is None:
        if request.master_pin is not None:
            raise RuntimeError("authored_master_resolution_missing")
        internal = build_internal_authored_composition(
            owner_id=owner_id,
            style=style,
            metadata=request._authored_deck_metadata,
        )
        await repository.append_imported_master(internal.master)
        resolved = internal.resolved
        composition_pin = internal.composition
    master = await repository.get_master(
        resolved.pin.master_id, resolved.pin.master_revision
    )
    if composition_pin is None:
        composition, _effective_contract = resolve_authored_composition(
            master.contract,
            style=style,
            visual_direction=master.visual_direction,
        )
    else:
        materialize_authored_composition(master.contract, composition_pin)
        composition = composition_pin
    if revision != 1:
        raise ValueError("normal Authored semantic deck must start at revision 1")
    # Author/render checkpoints survive process restarts, while the DOM metrics
    # cache is intentionally process-local.  Recollect the exact instrumented
    # HTML before Scene compilation so a retry cannot publish every owner as
    # geometry-unavailable merely because its PNG outlived the worker process.
    if browser_metrics_by_slide is not None:
        telemetry = tuple(browser_metrics_by_slide)
    else:
        with set_render_deck_scope(str(presentation_id)):
            telemetry = await asyncio.gather(
                *(
                    captured_metrics_or_existing(html, index=index, ensure=ensure_browser_metrics)
                    for index, html in enumerate(htmls)
                )
            )
    if any(metrics is None for metrics in telemetry):
        raise ValueError("editable Hybrid Scene browser telemetry is unavailable")
    deck_id = recovery_deck_id or f"deck:semantic-{presentation_id.hex}"
    slide_ids = [f"slide:legacy-{index + 1}" for index in range(len(htmls))]
    scene = build_editable_hybrid_scene(
        presentation_id=str(presentation_id),
        revision=revision,
        slides=None,
        slide_ids=slide_ids,
        deck_id=deck_id,
        htmls=htmls,
        pngs=pngs,
        semantic_drafts=semantic_components,
        composition_digest=composition.digest,
        source_digest_override=scene_envelope.scene_digest,
        browser_metrics_by_slide=telemetry,
        document_canvas=(
            request._document_canvas.model_dump(mode="json", by_alias=True)
            if getattr(request, "_document_canvas", None) is not None else None
        ),
    )
    sealed_slides: list[ValidatedSlideSpecV2] = []
    for index, (html, scene_slide) in enumerate(
        zip(htmls, scene.slides, strict=True)
    ):
        projected_title, projected_body = project_authored_owned_text(html)
        fallback = " ".join(outline.slides[index].content.split()).strip()
        title_text = projected_title or fallback or f"Slide {index + 1}"
        body_text = "\n".join(projected_body).strip() or fallback or title_text
        layout = (
            master.contract.layouts.cover
            if index == 0
            else master.contract.layouts.standard
        )
        body_slot = next(
            (slot for slot in layout.slots if slot.role == "body"), None
        )
        title = ExplicitTitle(text=title_text, lines=[title_text])
        title_fit = TitleFitResult(
            layout_id=layout.id,
            explicit_lines=title.lines,
            font_family=master.contract.font_manifest.windows_fallback,
            font_manifest_digest=resolved.pin.font_manifest_digest,
            chosen_font_pt=master.contract.semantic_styles.title.font_pt,
            margins=master.contract.semantic_styles.title.margins,
            clipped=False,
        )
        content_digest = canonical_digest(
            {
                "title": title.model_dump(mode="json", by_alias=True),
                "body": body_text,
            }
        )
        slide_payload = {
            "schemaVersion": "slidespec/v2",
            "slideId": scene_slide.slide_id,
            "revision": revision,
            "layoutId": layout.id,
            "title": title.model_dump(mode="json", by_alias=True),
            "body": body_text,
            "ownership": {"locked": False, "modelWritable": True},
            "masterDigest": resolved.pin.master_digest,
            "titleFit": title_fit.model_dump(mode="json", by_alias=True),
            "contentDigest": content_digest,
        }
        slide = SlideSpecV2(
            **slide_payload,
            specDigest=canonical_digest(slide_payload),
        )
        visual_plan_digest = canonical_digest(
            {
                "schemaVersion": "authored-semantic-import-plan/v1",
                "slideId": scene_slide.slide_id,
                "sourceDigest": scene.source_digest,
                "sceneDigest": scene_slide.slide_digest,
                "ownedTextDigest": content_digest,
            }
        )
        sealed_slides.append(
            ValidatedSlideSpecV2(
                slide=slide,
                checkpoint=SlideValidationCheckpoint(
                    master_digest=resolved.pin.master_digest,
                    layout_registry_digest=resolved.pin.layout_registry_digest,
                    font_manifest_digest=resolved.pin.font_manifest_digest,
                    source_digest=scene.source_digest,
                    composition=composition,
                    visual_plan_digest=visual_plan_digest,
                    scene_digest=scene_slide.slide_digest,
                    quality_policy_version="authored-semantic-browser/v1",
                    layout_id=layout.id,
                    body_slot_id=(
                        body_slot.id if body_slot is not None else "slot:semantic-body"
                    ),
                    validated_at=datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                ),
                scene=scene_slide,
            )
        )
    deck = _seal_deck(
        deck_id=deck_id,
        revision=revision,
        parent=None,
        pin=resolved.pin,
        composition=composition,
        source_digest=scene.source_digest,
        slides=sealed_slides,
        canonical_scene=scene,
    )
    if recovery_deck_id is not None:
        binding = await repository.run(
            _append_staging_retry_semantic_deck, deck, recovery_context
        )
        object.__setattr__(request, "_authored_staging_scene_binding", binding)
        return deck.scene
    try:
        return await repository.run(_persist_or_reuse_identical_semantic_deck, deck)
    except DeckAlreadyExistsError:
        if recovery_context is None:
            raise
        candidate_identity = canonical_digest(
            {
                "sceneEnvelope": scene_envelope.scene_digest,
                "pngDigests": [sha256(png).hexdigest() for png in pngs],
                # The sealed Scene carries all geometry.  Do not let transient
                # browser timing/cache counters create a new recovery identity.
                "sceneDigest": scene.scene_digest,
                "masterDigest": resolved.pin.master_digest,
                "compositionDigest": composition.digest,
            }
        )
        candidate_uuid = uuid.uuid5(
            uuid.NAMESPACE_URL,
            "presenton:authored:staging-recovery:"
            f"{presentation_id}:{recovery_context['job_id']}:{candidate_identity}",
        )
        return await _persist_master_pinned_editable_scene_deck(
            request=request,
            presentation_id=presentation_id,
            outline=outline,
            owner_id=owner_id,
            style=style,
            htmls=htmls,
            pngs=pngs,
            semantic_components=semantic_components,
            scene_envelope=scene_envelope,
            revision=1,
            browser_metrics_by_slide=telemetry,
            recovery_context=recovery_context,
            recovery_deck_id=f"deck:semantic-{candidate_uuid.hex}",
        )


_LOCAL_NAMES = frozenset(globals())

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if name not in _LOCAL_NAMES and not name.startswith("__"):
            globals()[name] = value
