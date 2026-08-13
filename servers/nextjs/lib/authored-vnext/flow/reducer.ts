import {
  AUTHORED_VNEXT_FLOW_SCHEMA_VERSION,
  AUTHORED_VNEXT_SLIDE_COUNT,
  type AuthoredVnextFlowState,
  type FlowAvailability,
  type FlowCheckpoint,
  type FlowEffect,
  type FlowEvent,
  type FlowFault,
  type FlowTransition,
  type OutlineLayoutPlan,
  type PublishedMasterReference,
  type SlideGenerationProgress,
} from "./types.ts";

const DIGEST_PATTERN = /^sha256:[0-9a-f]{64}$/;

export function createAuthoredVnextFlowState(): AuthoredVnextFlowState {
  return {
    schemaVersion: AUTHORED_VNEXT_FLOW_SCHEMA_VERSION,
    mode: "authored-vnext",
    featureEnabled: true,
    step: "materials",
    materials: [],
    availability: { status: "empty" },
  };
}

function checkpoint(state: AuthoredVnextFlowState): FlowCheckpoint {
  return {
    step: state.step,
    materials: state.materials,
    ...(state.masterDraft ? { masterDraft: state.masterDraft } : {}),
    ...(state.publishedMaster ? { publishedMaster: state.publishedMaster } : {}),
    ...(state.visualDirection ? { visualDirection: state.visualDirection } : {}),
    ...(state.outlineLayout ? { outlineLayout: state.outlineLayout } : {}),
    ...(state.generation ? { generation: state.generation } : {}),
    ...(state.exportRequest ? { exportRequest: state.exportRequest } : {}),
  };
}

function latestKnownGood(state: AuthoredVnextFlowState): FlowCheckpoint | undefined {
  switch (state.availability.status) {
    case "ready":
      return state.availability.value;
    case "loading":
    case "stale":
    case "error":
      return state.availability.lastKnownGood;
    case "empty":
      return undefined;
  }
}

function withCheckpoint(
  state: AuthoredVnextFlowState,
  next: FlowCheckpoint,
  availability: FlowAvailability = state.availability,
): AuthoredVnextFlowState {
  return {
    schemaVersion: state.schemaVersion,
    mode: state.mode,
    featureEnabled: state.featureEnabled,
    ...next,
    availability,
  };
}

function reject(state: AuthoredVnextFlowState, event: FlowEvent, reason: string): FlowTransition {
  return {
    state,
    effects: [{ type: "flow.transitionRejected", eventType: event.type, step: state.step, reason }],
  };
}

function fail(state: AuthoredVnextFlowState, error: FlowFault): AuthoredVnextFlowState {
  return {
    ...state,
    availability: {
      status: "error",
      error,
      lastKnownGood: latestKnownGood(state) ?? checkpoint(state),
    },
  };
}

function validPublishedMaster(master: PublishedMasterReference | undefined): master is PublishedMasterReference {
  return Boolean(
    master &&
      master.status === "published" &&
      master.masterId.length > 0 &&
      Number.isInteger(master.revision) &&
      master.revision > 0 &&
      DIGEST_PATTERN.test(master.digest),
  );
}

export function validateOutlineLayout(plan: OutlineLayoutPlan): readonly string[] {
  const errors: string[] = [];
  if (plan.slides.length !== AUTHORED_VNEXT_SLIDE_COUNT) {
    errors.push(`outline_requires_${AUTHORED_VNEXT_SLIDE_COUNT}_slides`);
  }
  const slideIds = new Set<string>();
  const ordinals = new Set<number>();
  for (const slide of plan.slides) {
    if (!slide.slideId || slideIds.has(slide.slideId)) errors.push("outline_slide_id_not_stable_unique");
    if (!Number.isInteger(slide.ordinal) || slide.ordinal < 1 || slide.ordinal > AUTHORED_VNEXT_SLIDE_COUNT || ordinals.has(slide.ordinal)) {
      errors.push("outline_ordinal_invalid");
    }
    if (!slide.layoutId) errors.push("outline_layout_required");
    slideIds.add(slide.slideId);
    ordinals.add(slide.ordinal);
  }
  return [...new Set(errors)];
}

function makeProgress(plan: OutlineLayoutPlan): readonly SlideGenerationProgress[] {
  return [...plan.slides]
    .sort((left, right) => left.ordinal - right.ordinal)
    .map((slide) => ({
      slideId: slide.slideId,
      ordinal: slide.ordinal,
      status: "queued" as const,
      percent: 0,
    }));
}

function canRequestOutline(state: AuthoredVnextFlowState): boolean {
  return validPublishedMaster(state.publishedMaster) && Boolean(state.visualDirection) && state.materials.length > 0;
}

function readyForGeneration(state: AuthoredVnextFlowState): boolean {
  return canRequestOutline(state) && Boolean(state.outlineLayout && validateOutlineLayout(state.outlineLayout).length === 0);
}

function exhaustive(event: never): never {
  throw new Error(`Unhandled authored vNext flow event: ${JSON.stringify(event)}`);
}

export function authoredVnextFlowReducer(
  state: AuthoredVnextFlowState,
  event: FlowEvent,
): FlowTransition {
  switch (event.type) {
    case "flow.loadRequested": {
      const lkg = latestKnownGood(state) ?? (state.availability.status === "empty" ? undefined : checkpoint(state));
      return {
        state: {
          ...state,
          availability: { status: "loading", ...(lkg ? { lastKnownGood: lkg } : {}) },
        },
        effects: [{ type: "flow.load" }],
      };
    }
    case "flow.loaded": {
      return {
        state: withCheckpoint(state, event.checkpoint, {
          status: "ready",
          refreshedAt: event.refreshedAt,
          value: event.checkpoint,
        }),
        effects: [],
      };
    }
    case "flow.loadFailed":
      return { state: fail(state, event.error), effects: [] };
    case "flow.markedStale": {
      const current = checkpoint(state);
      return {
        state: {
          ...state,
          availability: {
            status: "stale",
            reason: event.reason,
            detectedAt: event.detectedAt,
            value: current,
            lastKnownGood: latestKnownGood(state) ?? current,
          },
        },
        effects: [],
      };
    }
    case "flow.lastKnownGoodRestored": {
      const lkg = latestKnownGood(state);
      if (!lkg) return reject(state, event, "last_known_good_unavailable");
      return {
        state: withCheckpoint(state, lkg, {
          status: "ready",
          refreshedAt: event.refreshedAt,
          value: lkg,
        }),
        effects: [],
      };
    }
    case "materials.confirmed": {
      if (state.step !== "materials") return reject(state, event, "materials_step_required");
      if (event.materials.length === 0) return reject(state, event, "at_least_one_material_required");
      return {
        state: { ...state, materials: event.materials, step: "master" },
        effects: [],
      };
    }
    case "master.draftSelected": {
      if (state.step !== "master") return reject(state, event, "master_step_required");
      return { state: { ...state, masterDraft: event.draft, publishedMaster: undefined }, effects: [] };
    }
    case "master.publishRequested": {
      if (state.step !== "master") return reject(state, event, "master_step_required");
      if (!state.masterDraft) return reject(state, event, "master_draft_required");
      if (!state.masterDraft.validation.valid) return reject(state, event, "master_validation_must_pass");
      return {
        state,
        effects: [{
          type: "master.publish",
          command: {
            masterId: state.masterDraft.masterId,
            expectedRevision: state.masterDraft.revision,
            expectedDigest: state.masterDraft.digest,
          },
        }],
      };
    }
    case "master.publishSucceeded": {
      if (state.step !== "master" || !state.masterDraft) return reject(state, event, "master_publish_was_not_expected");
      if (!validPublishedMaster(event.published)) return reject(state, event, "published_master_reference_invalid");
      if (event.published.masterId !== state.masterDraft.masterId || event.published.revision <= state.masterDraft.revision) {
        return reject(state, event, "published_master_cas_mismatch");
      }
      return {
        state: { ...state, publishedMaster: event.published, step: "visualDirection" },
        effects: [],
      };
    }
    case "master.publishFailed":
      return { state: fail(state, event.error), effects: [] };
    case "visualDirection.confirmed": {
      if (state.step !== "visualDirection") return reject(state, event, "visual_direction_step_required");
      if (!validPublishedMaster(state.publishedMaster)) return reject(state, event, "published_master_required");
      if (!event.visualDirection.directionId || event.visualDirection.tone.length === 0) {
        return reject(state, event, "visual_direction_incomplete");
      }
      const next = { ...state, visualDirection: event.visualDirection, step: "outlineLayout" as const };
      if (!canRequestOutline(next)) return reject(state, event, "outline_prerequisites_missing");
      return {
        state: next,
        effects: [{
          type: "outlineLayout.request",
          command: {
            materials: next.materials,
            master: state.publishedMaster,
            visualDirection: event.visualDirection,
            slideCount: AUTHORED_VNEXT_SLIDE_COUNT,
          },
        }],
      };
    }
    case "outline.succeeded": {
      if (state.step !== "outlineLayout") return reject(state, event, "outline_layout_step_required");
      if (!canRequestOutline(state)) return reject(state, event, "published_master_and_visual_direction_required");
      const errors = validateOutlineLayout(event.plan);
      if (errors.length > 0) return reject(state, event, errors.join(","));
      return { state: { ...state, outlineLayout: event.plan }, effects: [] };
    }
    case "outline.failed":
      return { state: fail(state, event.error), effects: [] };
    case "generation.requested": {
      if (state.step !== "outlineLayout") return reject(state, event, "outline_layout_step_required");
      if (!readyForGeneration(state) || !state.publishedMaster || !state.visualDirection || !state.outlineLayout) {
        return reject(state, event, "published_master_visual_direction_and_ten_slide_outline_required");
      }
      const generation = {
        runId: event.runId,
        deckId: event.deckId,
        expectedDeckRevision: event.expectedDeckRevision,
        expectedDeckDigest: event.expectedDeckDigest,
        slides: makeProgress(state.outlineLayout),
      };
      return {
        state: { ...state, generation, step: "generating" },
        effects: [{
          type: "deck.generate",
          command: {
            runId: event.runId,
            deckId: event.deckId,
            expectedDeckRevision: event.expectedDeckRevision,
            expectedDeckDigest: event.expectedDeckDigest,
            master: state.publishedMaster,
            visualDirection: state.visualDirection,
            outlineLayout: state.outlineLayout,
          },
        }],
      };
    }
    case "generation.slideProgressed": {
      if (state.step !== "generating" || !state.generation) return reject(state, event, "active_generation_required");
      const index = state.generation.slides.findIndex((slide) => slide.slideId === event.progress.slideId);
      if (index < 0 || state.generation.slides[index].ordinal !== event.progress.ordinal) {
        return reject(state, event, "generation_slide_identity_mismatch");
      }
      if (event.progress.percent < 0 || event.progress.percent > 100) return reject(state, event, "generation_percent_invalid");
      const previous = state.generation.slides[index];
      if (event.progress.percent < previous.percent || previous.status === "ready") {
        return reject(state, event, "generation_progress_must_be_monotonic");
      }
      const slides = state.generation.slides.map((slide, slideIndex) =>
        slideIndex === index ? event.progress : slide,
      );
      const allReady = slides.every((slide) => slide.status === "ready" && slide.percent === 100 && slide.slideRevision !== undefined);
      return {
        state: {
          ...state,
          generation: { ...state.generation, slides },
          step: allReady ? "workbench" : "generating",
        },
        effects: [],
      };
    }
    case "generation.failed":
      return { state: fail(state, event.error), effects: [] };
    case "workbench.opened": {
      if (!state.generation?.slides.every((slide) => slide.status === "ready")) {
        return reject(state, event, "ten_generated_slides_required");
      }
      return { state: { ...state, step: "workbench" }, effects: [] };
    }
    case "export.requested": {
      if (state.step !== "workbench" || !state.generation) return reject(state, event, "workbench_step_required");
      if (!state.generation.slides.every((slide) => slide.status === "ready" && slide.percent === 100)) {
        return reject(state, event, "completed_generation_required");
      }
      return {
        state: {
          ...state,
          step: "exportRequest",
          exportRequest: { requestId: event.requestId, format: event.format, status: "requesting" },
        },
        effects: [{
          type: "deck.exportRequest",
          command: {
            requestId: event.requestId,
            deckId: state.generation.deckId,
            expectedDeckRevision: state.generation.expectedDeckRevision,
            expectedDeckDigest: state.generation.expectedDeckDigest,
            format: event.format,
          },
        }],
      };
    }
    case "export.queued": {
      if (state.step !== "exportRequest" || state.exportRequest?.requestId !== event.requestId) {
        return reject(state, event, "matching_export_request_required");
      }
      return { state: { ...state, exportRequest: { ...state.exportRequest, status: "queued" } }, effects: [] };
    }
    case "export.failed": {
      if (state.exportRequest?.requestId !== event.requestId) return reject(state, event, "matching_export_request_required");
      return {
        state: {
          ...fail(state, event.error),
          exportRequest: { ...state.exportRequest, status: "failed", errorCode: event.error.code },
        },
        effects: [],
      };
    }
    default:
      return exhaustive(event);
  }
}

export function applyFlowEvents(
  initial: AuthoredVnextFlowState,
  events: readonly FlowEvent[],
): FlowTransition {
  let state = initial;
  const effects: FlowEffect[] = [];
  for (const event of events) {
    const transition = authoredVnextFlowReducer(state, event);
    state = transition.state;
    effects.push(...transition.effects);
  }
  return { state, effects };
}
