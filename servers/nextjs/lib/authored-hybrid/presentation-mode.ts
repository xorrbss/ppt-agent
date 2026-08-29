interface StoredPresentationIdentity {
  mode?: unknown;
  theme?: unknown;
  slides?: unknown;
}

export const EDITABLE_HYBRID_SHADOW_PROFILE_ID =
  "authored-editable-hybrid-ko-en-basic-v1";

/**
 * Explicit Hybrid without a Scene projection uses normal element-level native
 * admission. A present-but-invalid projection or an unknown profile remains
 * raster-only so broken authority can never broaden editability.
 */
export function boundedReleaseRasterOnly(
  profileId?: string,
  invalidSceneProjection = false,
): boolean {
  return invalidSceneProjection || (
    profileId !== undefined &&
    profileId !== EDITABLE_HYBRID_SHADOW_PROFILE_ID
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Explicit mode wins; old authored sentinels keep pre-mode rows working. */
export function isAuthoredPresentation(
  presentation: StoredPresentationIdentity
): boolean {
  if (presentation.mode !== undefined && presentation.mode !== null) {
    return presentation.mode === "authored";
  }
  if (isRecord(presentation.theme) && presentation.theme.mode === "authored") {
    return true;
  }
  if (!Array.isArray(presentation.slides)) return false;
  return presentation.slides.some(
    (slide) =>
      isRecord(slide) &&
      (slide.layout_group === "authored" ||
        (isRecord(slide.content) && slide.content.__authored__ === true))
  );
}

/**
 * Recover the bounded native profile only from the exact server-persisted
 * Scene projection. The public export request never supplies this authority.
 */
export function presentationEditableHybridProfileId(
  presentation: StoredPresentationIdentity,
): typeof EDITABLE_HYBRID_SHADOW_PROFILE_ID | undefined {
  if (!isRecord(presentation.theme)) return undefined;
  const projection = presentation.theme.authored_vnext;
  if (!isRecord(projection) || !isRecord(projection.scene)) return undefined;
  const scene = projection.scene;
  if (
    projection.schemaVersion !==
      "authored-editable-hybrid-scene-projection/v1" ||
    projection.profileId !== EDITABLE_HYBRID_SHADOW_PROFILE_ID ||
    projection.status !== "shadow" ||
    projection.reasonCode !== "editable_hybrid_mvp_completion_not_sealed" ||
    projection.promotionReady !== false ||
    projection.deploymentAuthorized !== false ||
    typeof projection.deckId !== "string" ||
    !/^deck:[a-z0-9][a-z0-9._-]*$/u.test(projection.deckId) ||
    !Number.isSafeInteger(projection.deckRevision) ||
    typeof projection.sceneDigest !== "string" ||
    typeof projection.sourceDigest !== "string" ||
    scene.schemaVersion !== "deck-scene-ir/v1" ||
    scene.deckId !== projection.deckId ||
    scene.revision !== projection.deckRevision ||
    scene.sceneDigest !== projection.sceneDigest ||
    scene.sourceDigest !== projection.sourceDigest ||
    scene.hybridProfileId !== EDITABLE_HYBRID_SHADOW_PROFILE_ID ||
    !Array.isArray(scene.slides) ||
    scene.slides.length === 0
  ) return undefined;

  for (const slide of scene.slides) {
    if (!isRecord(slide) || !Array.isArray(slide.elements)) return undefined;
    const plan = slide.hybridExport;
    if (
      !isRecord(plan) ||
      plan.schemaVersion !== "slide-hybrid-export-plan/v1" ||
      !Array.isArray(plan.ownerDecisions) ||
      plan.ownerDecisions.length !== slide.elements.length
    ) return undefined;
    const elementIds = new Set(
      slide.elements.flatMap((element) =>
        isRecord(element) && typeof element.elementId === "string"
          ? [element.elementId]
          : [],
      ),
    );
    const ownerIds = new Set<string>();
    for (const decision of plan.ownerDecisions) {
      if (
        !isRecord(decision) ||
        typeof decision.ownerId !== "string" ||
        !elementIds.has(decision.ownerId) ||
        ownerIds.has(decision.ownerId) ||
        decision.decision === "publicationBlocked" ||
        (decision.decision !== "nativeEditable" &&
          decision.decision !== "rasterIsland")
      ) return undefined;
      ownerIds.add(decision.ownerId);
    }
    if (ownerIds.size !== elementIds.size) return undefined;
  }
  return EDITABLE_HYBRID_SHADOW_PROFILE_ID;
}

/**
 * Standard explicit Hybrid exports use the semantic/QID and element-level
 * admission gates. If a presentation does carry an authored-vNext projection,
 * however, that projection becomes authority and any malformed or blocked
 * projection keeps the whole deck raster-only.
 */
export function presentationRequiresRasterOnlyHybrid(
  presentation: StoredPresentationIdentity,
): boolean {
  if (!isRecord(presentation.theme)) return false;
  const projection = presentation.theme.authored_vnext;
  if (!isRecord(projection)) return false;
  const scene = projection.scene;
  const claimsEditableHybridAuthority =
    projection.schemaVersion ===
      "authored-editable-hybrid-scene-projection/v1" ||
    projection.profileId !== undefined ||
    (isRecord(scene) && scene.hybridProfileId !== undefined);
  if (!claimsEditableHybridAuthority) return false;
  return presentationEditableHybridProfileId(presentation) === undefined;
}
