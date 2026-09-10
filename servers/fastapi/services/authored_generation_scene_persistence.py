"""Extracted implementation behind the authored generation facade."""

from __future__ import annotations


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
        raise DeckAlreadyExistsError()
    return current.scene

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
    repository = generation_repository_for_owner(owner_id)
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
        repository.append_imported_master(internal.master)
        resolved = internal.resolved
        composition_pin = internal.composition
    master = repository.get_master(
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
            telemetry = await asyncio.gather(*(ensure_browser_metrics(html) for html in htmls))
    if any(metrics is None for metrics in telemetry):
        raise ValueError("editable Hybrid Scene browser telemetry is unavailable")
    deck_id = f"deck:semantic-{presentation_id.hex}"
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
    return await asyncio.to_thread(
        _persist_or_reuse_identical_semantic_deck,
        repository,
        deck,
    )


_LOCAL_NAMES = frozenset(globals())

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if name not in _LOCAL_NAMES and not name.startswith("__"):
            globals()[name] = value
