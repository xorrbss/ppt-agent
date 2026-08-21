import {
  createEmptyWorkbenchState,
  lastKnownGood,
  type AssetBodyNode,
  type BodyNode,
  type Bounds,
  type CasPin,
  type DeckDocument,
  type DocumentPatch,
  type ElementId,
  type HistoryEntry,
  type OperationId,
  type OutboxEntry,
  type RegenerateRequest,
  type SlideDocument,
  type SlideId,
  type WorkbenchEffect,
  type WorkbenchError,
  type WorkbenchOperation,
  type WorkbenchState,
} from "./model.ts";

export type WorkbenchAction =
  | { readonly type: "deck/loadStarted" }
  | { readonly type: "deck/loadSucceeded"; readonly deck: DeckDocument }
  | { readonly type: "deck/loadEmpty" }
  | { readonly type: "deck/loadFailed"; readonly error: WorkbenchError }
  | { readonly type: "deck/markStale"; readonly reason: string }
  | { readonly type: "slide/activate"; readonly slideId: SlideId }
  | { readonly type: "operation/enqueue"; readonly operation: WorkbenchOperation; readonly createdAt: string }
  | { readonly type: "operation/sendStarted"; readonly operationId: OperationId }
  | { readonly type: "operation/acknowledged"; readonly operationId: OperationId; readonly serverDeck: DeckDocument }
  | {
      readonly type: "operation/failed";
      readonly operationId: OperationId;
      readonly code: string;
      readonly serverDeck?: DeckDocument;
    }
  | {
      readonly type: "operation/conflicted";
      readonly operationId: OperationId;
      readonly code: string;
      readonly actual?: { readonly deckRevision: number; readonly deckDigest: DeckDocument["digest"] };
    }
  | { readonly type: "conflict/rebaseStarted" }
  | { readonly type: "conflict/rebaseSucceeded"; readonly serverDeck: DeckDocument }
  | { readonly type: "conflict/rebaseFailed"; readonly message: string }
  | { readonly type: "conflict/discarded"; readonly serverDeck: DeckDocument }
  | { readonly type: "history/undo"; readonly operationId: OperationId; readonly createdAt: string }
  | { readonly type: "history/redo"; readonly operationId: OperationId; readonly createdAt: string }
  | { readonly type: "regeneration/requested"; readonly request: RegenerateRequest }
  | {
      readonly type: "regeneration/succeeded";
      readonly requestId: string;
      readonly candidateDigest: DeckDocument["digest"];
      readonly serverDeck: DeckDocument;
    }
  | { readonly type: "regeneration/failed"; readonly requestId: string; readonly error: WorkbenchError }
  | { readonly type: "regeneration/accept" }
  | { readonly type: "regeneration/reject" };

export class WorkbenchInvariantError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkbenchInvariantError";
  }
}

const NATIVE_CHART_TYPES = new Set(["bar", "column", "line", "area", "pie", "donut"]);

function assertSealedDataPayload(
  node: Extract<BodyNode, { kind: "table" | "chart" }>,
  operation: Extract<WorkbenchOperation, { kind: "data.set" }>,
): void {
  if (operation.dataId !== node.dataId) {
    throw new WorkbenchInvariantError("Data operation must preserve the stable Scene data id");
  }
  const payload = operation.dataPayload;
  if (node.kind === "table") {
    if (operation.chartType !== undefined || operation.accessibilitySummary !== undefined) {
      throw new WorkbenchInvariantError("Table operation cannot change chart-only fields");
    }
    const cells = payload.cells;
    if (!Array.isArray(cells) || cells.length === 0 || cells.length > 100 || !cells.every(Array.isArray)) {
      throw new WorkbenchInvariantError("Table data requires a non-empty bounded cell matrix");
    }
    const columns = cells[0]?.length ?? 0;
    if (
      columns < 1 || columns > 30 ||
      cells.some((row) => row.length !== columns || row.some((cell) => typeof cell !== "string" || cell.length > 5000)) ||
      (operation.rows !== undefined && operation.rows !== cells.length) ||
      (operation.columns !== undefined && operation.columns !== columns)
    ) {
      throw new WorkbenchInvariantError("Table data must retain its bounded rectangular dimensions");
    }
    return;
  }
  if (operation.rows !== undefined || operation.columns !== undefined) {
    throw new WorkbenchInvariantError("Chart operation cannot change table-only dimensions");
  }
  if (operation.chartType !== undefined && !NATIVE_CHART_TYPES.has(operation.chartType)) {
    throw new WorkbenchInvariantError("Chart operation requires a native authored chart type");
  }
  const categories = payload.categories;
  const series = payload.series;
  if (
    !Array.isArray(categories) || categories.length === 0 || categories.length > 100 ||
    categories.some((category) => typeof category !== "string" || !category || category.length > 500) ||
    !Array.isArray(series) || series.length === 0 || series.length > 30
  ) {
    throw new WorkbenchInvariantError("Chart data requires bounded categories and series");
  }
  const names = new Set<string>();
  for (const entry of series) {
    if (
      !entry || typeof entry !== "object" || Array.isArray(entry) ||
      Object.keys(entry as Record<string, unknown>).length !== 2 ||
      !("name" in entry) || !("values" in entry) ||
      typeof (entry as { name?: unknown }).name !== "string" ||
      !(entry as { name: string }).name || (entry as { name: string }).name.length > 500 ||
      !Array.isArray((entry as { values?: unknown }).values) ||
      (entry as { values: unknown[] }).values.length !== categories.length ||
      (entry as { values: unknown[] }).values.some((value) => typeof value !== "number" || !Number.isFinite(value))
    ) {
      throw new WorkbenchInvariantError("Chart series must retain bounded numeric values");
    }
    const name = (entry as { name: string }).name;
    if (names.has(name)) throw new WorkbenchInvariantError("Chart series names must be unique");
    names.add(name);
  }
  const chartType = operation.chartType ?? node.chartType;
  if ((chartType === "pie" || chartType === "donut") && series.length !== 1) {
    throw new WorkbenchInvariantError("Pie and donut charts require exactly one series");
  }
}

function editableDeck(state: WorkbenchState): DeckDocument {
  if (state.deck.status !== "ready" && state.deck.status !== "stale") {
    throw new WorkbenchInvariantError("An editable deck is required");
  }
  return state.deck.data;
}

function findSlide(deck: DeckDocument, slideId: SlideId): SlideDocument {
  const found = deck.slides.find((slide) => slide.id === slideId);
  if (!found) throw new WorkbenchInvariantError(`Unknown stable slide id: ${slideId}`);
  return found;
}

function findNode(slide: SlideDocument, elementId: ElementId): BodyNode {
  const found = slide.nodes.find((node) => node.id === elementId);
  if (!found) throw new WorkbenchInvariantError(`Unknown stable element id: ${elementId}`);
  return found;
}

function sameBounds(left: Bounds, right: Bounds): boolean {
  return left.x === right.x && left.y === right.y && left.width === right.width && left.height === right.height;
}

function pinFor(deck: DeckDocument, slideId: SlideId): CasPin {
  const slide = findSlide(deck, slideId);
  return {
    deckRevision: deck.revision,
    deckDigest: deck.digest,
    sceneDigest: deck.sceneDigest,
    slideRevision: slide.revision,
    slideDigest: slide.digest,
    masterDigest: deck.master.digest,
  };
}

function assertPin(operation: WorkbenchOperation, deck: DeckDocument): void {
  const current = pinFor(deck, operation.slideId);
  if (
    operation.base.deckRevision !== current.deckRevision ||
    operation.base.deckDigest !== current.deckDigest ||
    operation.base.sceneDigest !== current.sceneDigest ||
    operation.base.slideRevision !== current.slideRevision ||
    operation.base.slideDigest !== current.slideDigest ||
    operation.base.masterDigest !== current.masterDigest
  ) {
    throw new WorkbenchInvariantError("Operation CAS pin does not match the working document");
  }
}

export function operationPatch(deck: DeckDocument, operation: WorkbenchOperation): DocumentPatch {
  assertPin(operation, deck);
  const slide = findSlide(deck, operation.slideId);
  if (operation.kind === "layout.apply") {
    return { kind: operation.kind, slideId: slide.id, before: slide.layoutId, after: operation.layoutId };
  }
  const node = findNode(slide, operation.elementId);
  if (operation.kind === "text.replace") {
    if (node.kind !== "text") throw new WorkbenchInvariantError("Text operation requires a text node");
    if (!node.editability.text) throw new WorkbenchInvariantError("Text operation is not permitted by the Scene capability");
    return { kind: operation.kind, slideId: slide.id, elementId: node.id, before: node.text, after: operation.text };
  }
  if (operation.kind === "geometry.update") {
    if (!node.editability.geometry) throw new WorkbenchInvariantError("Geometry operation is not permitted by the Scene capability");
    return { kind: operation.kind, slideId: slide.id, elementId: node.id, before: node.bounds, after: operation.bounds };
  }
  if (operation.kind === "asset.set") {
    if (node.kind !== "asset" && node.kind !== "image") throw new WorkbenchInvariantError("Asset operation requires an image node");
    if (!node.editability.asset) throw new WorkbenchInvariantError("Asset operation is not permitted by the Scene capability");
    if (!/^sha256:[0-9a-f]{64}$/.test(operation.digest)) throw new WorkbenchInvariantError("Asset operation requires a SHA-256 digest");
    if (!Number.isSafeInteger(operation.sizeBytes) || operation.sizeBytes <= 0) throw new WorkbenchInvariantError("Asset operation requires a verified byte length");
    if (
      (operation.assetId !== node.assetId || operation.digest !== node.digest) &&
      (!operation.mediaType || !operation.storageReference || operation.sizeBytes <= 0)
    ) {
      throw new WorkbenchInvariantError("New asset identity requires sealed media type and storage reference");
    }
    return {
      kind: operation.kind,
      slideId: slide.id,
      elementId: node.id,
      before: { assetId: node.assetId, digest: node.digest, altText: node.altText, crop: node.crop, fit: node.fit, mediaType: node.mediaType, storageReference: node.storageReference, sizeBytes: node.sizeBytes ?? operation.sizeBytes },
      after: { assetId: operation.assetId, digest: operation.digest, altText: operation.altText, crop: operation.crop, fit: operation.crop, mediaType: operation.mediaType, storageReference: operation.storageReference, sizeBytes: operation.sizeBytes },
    };
  }
  if (node.kind !== "table" && node.kind !== "chart") throw new WorkbenchInvariantError("Data operation requires a table or chart node");
  if (!node.editability.data) throw new WorkbenchInvariantError("Data operation is not permitted by the Scene capability");
  if (!/^sha256:[0-9a-f]{64}$/.test(operation.dataDigest)) throw new WorkbenchInvariantError("Data operation requires a SHA-256 digest");
  assertSealedDataPayload(node, operation);
  return {
    kind: operation.kind,
    slideId: slide.id,
    elementId: node.id,
    before: { dataId: node.dataId, dataDigest: node.dataDigest, dataPayload: node.dataPayload ?? {} },
    after: {
      dataId: operation.dataId,
      dataDigest: operation.dataDigest,
      dataPayload: operation.dataPayload,
      ...(operation.rows === undefined ? {} : { rows: operation.rows }),
      ...(operation.columns === undefined ? {} : { columns: operation.columns }),
      ...(operation.chartType === undefined ? {} : { chartType: operation.chartType }),
      ...(operation.accessibilitySummary === undefined ? {} : { accessibilitySummary: operation.accessibilitySummary }),
    },
  };
}

function updateSlide(deck: DeckDocument, slideId: SlideId, update: (slide: SlideDocument) => SlideDocument): DeckDocument {
  let matched = false;
  const slides = deck.slides.map((slide) => {
    if (slide.id !== slideId) return slide;
    matched = true;
    return update(slide);
  });
  if (!matched) throw new WorkbenchInvariantError(`Unknown stable slide id: ${slideId}`);
  return { ...deck, slides };
}

function updateNode(slide: SlideDocument, elementId: ElementId, update: (node: BodyNode) => BodyNode): SlideDocument {
  let matched = false;
  const nodes = slide.nodes.map((node) => {
    if (node.id !== elementId) return node;
    matched = true;
    return update(node);
  });
  if (!matched) throw new WorkbenchInvariantError(`Unknown stable element id: ${elementId}`);
  return { ...slide, nodes };
}

export function applyPatch(deck: DeckDocument, patch: DocumentPatch): DeckDocument {
  return updateSlide(deck, patch.slideId, (slide) => {
    if (patch.kind === "layout.apply") return { ...slide, layoutId: patch.after };
    return updateNode(slide, patch.elementId, (node) => {
      if (patch.kind === "text.replace") {
        if (node.kind !== "text") throw new WorkbenchInvariantError("Text patch requires a text node");
        return { ...node, text: patch.after };
      }
      if (patch.kind === "geometry.update") {
        return sameBounds(node.bounds, patch.after) ? node : { ...node, bounds: patch.after };
      }
      if (patch.kind === "asset.set") {
        if (node.kind !== "asset" && node.kind !== "image") throw new WorkbenchInvariantError("Asset patch requires an image node");
        return { ...node, ...patch.after } satisfies AssetBodyNode;
      }
      if (node.kind !== "table" && node.kind !== "chart") throw new WorkbenchInvariantError("Data patch requires a table or chart node");
      return {
        ...node,
        dataId: patch.after.dataId,
        dataDigest: patch.after.dataDigest,
        dataPayload: patch.after.dataPayload,
        ...(node.kind === "table" ? { rows: patch.after.rows ?? node.rows, columns: patch.after.columns ?? node.columns } : {}),
        ...(node.kind === "chart" ? { chartType: patch.after.chartType ?? node.chartType, accessibilitySummary: patch.after.accessibilitySummary ?? node.accessibilitySummary } : {}),
      };
    });
  });
}

export function invertPatch(patch: DocumentPatch): DocumentPatch {
  return { ...patch, before: patch.after, after: patch.before } as DocumentPatch;
}

function withBase(operation: WorkbenchOperation, base: CasPin): WorkbenchOperation {
  return { ...operation, base };
}

function operationFromPatch(patch: DocumentPatch, operationId: OperationId, base: CasPin): WorkbenchOperation {
  if (patch.kind === "text.replace") {
    return { kind: patch.kind, operationId, slideId: patch.slideId, elementId: patch.elementId, text: patch.after, base };
  }
  if (patch.kind === "geometry.update") {
    return { kind: patch.kind, operationId, slideId: patch.slideId, elementId: patch.elementId, bounds: patch.after, base };
  }
  if (patch.kind === "asset.set") {
    if (patch.after.sizeBytes === undefined) throw new WorkbenchInvariantError("Asset patch must retain its verified byte length");
    return { kind: patch.kind, operationId, slideId: patch.slideId, elementId: patch.elementId, ...patch.after, sizeBytes: patch.after.sizeBytes, base };
  }
  if (patch.kind === "data.set") {
    if (patch.after.dataPayload === undefined) throw new WorkbenchInvariantError("Data patch must retain its sealed payload");
    return {
      kind: patch.kind,
      operationId,
      slideId: patch.slideId,
      elementId: patch.elementId,
      dataId: patch.after.dataId,
      dataDigest: patch.after.dataDigest,
      dataPayload: patch.after.dataPayload,
      ...(patch.after.rows === undefined ? {} : { rows: patch.after.rows }),
      ...(patch.after.columns === undefined ? {} : { columns: patch.after.columns }),
      ...(patch.after.chartType === undefined ? {} : { chartType: patch.after.chartType }),
      ...(patch.after.accessibilitySummary === undefined ? {} : { accessibilitySummary: patch.after.accessibilitySummary }),
      base,
    };
  }
  return { kind: patch.kind, operationId, slideId: patch.slideId, layoutId: patch.after, base };
}

function replayOutbox(serverDeck: DeckDocument, entries: readonly OutboxEntry[]): { deck: DeckDocument; outbox: readonly OutboxEntry[] } {
  let deck = serverDeck;
  const outbox = entries.map((entry) => {
    if (entry.status === "failed") return entry;
    const base = pinFor(serverDeck, entry.operation.slideId);
    deck = applyPatch(deck, entry.patch);
    return { ...entry, operation: withBase(entry.operation, base), status: "queued" as const, errorCode: undefined };
  });
  return { deck, outbox };
}

function assertPinnedMaster(current: DeckDocument | undefined, serverDeck: DeckDocument): void {
  if (current && (current.master.id !== serverDeck.master.id || current.master.digest !== serverDeck.master.digest)) {
    throw new WorkbenchInvariantError("The locked master changed; typed body operations cannot cross this boundary");
  }
}

function enqueueHistoryPatch(
  state: WorkbenchState,
  patch: DocumentPatch,
  operationId: OperationId,
  createdAt: string,
  source: "undo" | "redo",
): { deck: DeckDocument; entry: OutboxEntry } {
  const deck = editableDeck(state);
  const operation = operationFromPatch(patch, operationId, pinFor(deck, patch.slideId));
  return {
    deck: applyPatch(deck, patch),
    entry: { operation, patch, status: "queued", source, createdAt },
  };
}

export function workbenchReducer(state: WorkbenchState, action: WorkbenchAction): WorkbenchState {
  switch (action.type) {
    case "deck/loadStarted": {
      const lkg = lastKnownGood(state.deck);
      return { ...state, deck: lkg ? { status: "loading", lastKnownGood: lkg } : { status: "loading" } };
    }
    case "deck/loadSucceeded":
      return {
        ...state,
        deck: { status: "ready", data: action.deck },
        activeSlideId: state.activeSlideId && action.deck.slides.some((slide) => slide.id === state.activeSlideId)
          ? state.activeSlideId
          : action.deck.slides[0]?.id ?? null,
        conflict: null,
      };
    case "deck/loadEmpty":
      return { ...createEmptyWorkbenchState() };
    case "deck/loadFailed": {
      const lkg = lastKnownGood(state.deck);
      return { ...state, deck: lkg ? { status: "error", error: action.error, lastKnownGood: lkg } : { status: "error", error: action.error } };
    }
    case "deck/markStale": {
      const deck = lastKnownGood(state.deck);
      return deck ? { ...state, deck: { status: "stale", data: deck, reason: action.reason } } : state;
    }
    case "slide/activate": {
      const deck = lastKnownGood(state.deck);
      return deck?.slides.some((slide) => slide.id === action.slideId) ? { ...state, activeSlideId: action.slideId } : state;
    }
    case "operation/enqueue": {
      if (state.conflict) throw new WorkbenchInvariantError("Resolve the CAS conflict before editing");
      const deck = editableDeck(state);
      if (state.outbox.some((entry) => entry.operation.operationId === action.operation.operationId)) {
        throw new WorkbenchInvariantError("Operation id must be stable and unique");
      }
      const patch = operationPatch(deck, action.operation);
      const nextDeck = applyPatch(deck, patch);
      const inverse = invertPatch(patch);
      const history: HistoryEntry = { operationId: action.operation.operationId, forward: [patch], inverse: [inverse] };
      return {
        ...state,
        deck: { status: "ready", data: nextDeck },
        outbox: [...state.outbox, { operation: action.operation, patch, status: "queued", source: "user", createdAt: action.createdAt }],
        undoStack: [...state.undoStack, history],
        redoStack: [],
      };
    }
    case "operation/sendStarted":
      return { ...state, outbox: state.outbox.map((entry) => entry.operation.operationId === action.operationId ? { ...entry, status: "sending" } : entry) };
    case "operation/failed": {
      if (!state.outbox.some((entry) => entry.operation.operationId === action.operationId)) return state;
      const outbox = state.outbox.map((entry) => entry.operation.operationId === action.operationId
        ? { ...entry, status: "failed" as const, errorCode: action.code }
        : entry,
      );
      if (!action.serverDeck) return { ...state, outbox };
      assertPinnedMaster(lastKnownGood(state.deck), action.serverDeck);
      const replayed = replayOutbox(action.serverDeck, outbox);
      return { ...state, deck: { status: "ready", data: replayed.deck }, outbox: replayed.outbox, conflict: null };
    }
    case "operation/acknowledged": {
      const acknowledged = state.outbox.some((entry) => entry.operation.operationId === action.operationId);
      if (!acknowledged) return state;
      assertPinnedMaster(lastKnownGood(state.deck), action.serverDeck);
      const pending = state.outbox.filter((entry) => entry.operation.operationId !== action.operationId);
      const replayed = replayOutbox(action.serverDeck, pending);
      return { ...state, deck: { status: "ready", data: replayed.deck }, outbox: replayed.outbox, conflict: null };
    }
    case "operation/conflicted": {
      const entry = state.outbox.find((candidate) => candidate.operation.operationId === action.operationId);
      if (!entry) return state;
      return {
        ...state,
        outbox: state.outbox.map((candidate) => candidate === entry ? { ...candidate, status: "conflict" } : candidate),
        conflict: { operationId: action.operationId, code: action.code, expected: entry.operation.base, actual: action.actual, rebase: "required" },
      };
    }
    case "conflict/rebaseStarted":
      return state.conflict ? { ...state, conflict: { ...state.conflict, rebase: "rebasing" } } : state;
    case "conflict/rebaseSucceeded": {
      if (!state.conflict || state.conflict.rebase !== "rebasing") throw new WorkbenchInvariantError("Explicit rebase must be started first");
      try {
        assertPinnedMaster(lastKnownGood(state.deck), action.serverDeck);
        const replayed = replayOutbox(action.serverDeck, state.outbox);
        return { ...state, deck: { status: "ready", data: replayed.deck }, outbox: replayed.outbox, conflict: null };
      } catch (error) {
        return { ...state, conflict: { ...state.conflict, rebase: "failed", message: error instanceof Error ? error.message : "Rebase failed" } };
      }
    }
    case "conflict/rebaseFailed":
      return state.conflict ? { ...state, conflict: { ...state.conflict, rebase: "failed", message: action.message } } : state;
    case "conflict/discarded":
      return {
        ...state,
        deck: { status: "ready", data: action.serverDeck },
        outbox: state.conflict
          ? state.outbox.filter((entry) => entry.operation.operationId !== state.conflict?.operationId)
          : state.outbox,
        conflict: null,
      };
    case "history/undo": {
      const history = state.undoStack.at(-1);
      if (!history || history.inverse.length !== 1) return state;
      const queued = enqueueHistoryPatch(state, history.inverse[0], action.operationId, action.createdAt, "undo");
      return {
        ...state,
        deck: { status: "ready", data: queued.deck },
        outbox: [...state.outbox, queued.entry],
        undoStack: state.undoStack.slice(0, -1),
        redoStack: [...state.redoStack, history],
      };
    }
    case "history/redo": {
      const history = state.redoStack.at(-1);
      if (!history || history.forward.length !== 1) return state;
      const queued = enqueueHistoryPatch(state, history.forward[0], action.operationId, action.createdAt, "redo");
      return {
        ...state,
        deck: { status: "ready", data: queued.deck },
        outbox: [...state.outbox, queued.entry],
        undoStack: [...state.undoStack, history],
        redoStack: state.redoStack.slice(0, -1),
      };
    }
    case "regeneration/requested": {
      const deck = editableDeck(state);
      if (state.outbox.length > 0 || state.conflict) throw new WorkbenchInvariantError("Flush or rebase typed operations before regeneration");
      const current = pinFor(deck, action.request.slideId);
      if (
        action.request.scope !== "bodyOnly" ||
        action.request.pin.deckRevision !== current.deckRevision ||
        action.request.pin.deckDigest !== current.deckDigest ||
        action.request.pin.slideRevision !== current.slideRevision ||
        action.request.pin.masterDigest !== current.masterDigest ||
        findSlide(deck, action.request.slideId).layoutId !== action.request.layoutId
      ) {
        throw new WorkbenchInvariantError("Body-only regeneration CAS pin is stale");
      }
      return { ...state, regeneration: { status: "loading", request: action.request } };
    }
    case "regeneration/succeeded": {
      if (state.regeneration.status !== "loading" || state.regeneration.request.requestId !== action.requestId) return state;
      const baseline = editableDeck(state);
      const candidateSlide = findSlide(action.serverDeck, state.regeneration.request.slideId);
      if (action.serverDeck.master.digest !== baseline.master.digest || candidateSlide.layoutId !== state.regeneration.request.layoutId) {
        return {
          ...state,
          regeneration: {
            status: "conflict",
            request: state.regeneration.request,
            error: { code: "regeneration_scope_conflict", message: "Candidate changed the pinned master or layout", retryable: true },
          },
        };
      }
      return {
        ...state,
        regeneration: {
          status: "preview",
          candidate: { request: state.regeneration.request, candidateDigest: action.candidateDigest, candidateSlide, serverDeck: action.serverDeck, baseline },
        },
      };
    }
    case "regeneration/failed": {
      if (state.regeneration.status !== "loading" || state.regeneration.request.requestId !== action.requestId) return state;
      const conflict = action.error.code.includes("conflict") || action.error.code.includes("changed");
      return { ...state, regeneration: { status: conflict ? "conflict" : "error", request: state.regeneration.request, error: action.error } };
    }
    case "regeneration/accept":
      return state.regeneration.status === "preview"
        ? { ...state, deck: { status: "ready", data: state.regeneration.candidate.serverDeck }, undoStack: [], redoStack: [], regeneration: { status: "idle" } }
        : state;
    case "regeneration/reject":
      return state.regeneration.status === "preview"
        ? {
            ...state,
            deck: { status: "stale", data: state.regeneration.candidate.baseline, reason: "Rejected candidate; server revision is newer and requires reload or rebase" },
            regeneration: { status: "idle" },
          }
        : state;
  }
}

export function selectNextWorkbenchEffect(state: WorkbenchState): WorkbenchEffect | null {
  if (state.conflict) return null;
  if (state.outbox.some((entry) => entry.status === "sending")) return null;
  const operation = state.outbox.find((entry) => entry.status === "queued");
  if (operation) return { effect: "dispatchOperation", operation: operation.operation };
  if (state.regeneration.status === "loading") return { effect: "regenerateBody", request: state.regeneration.request };
  return null;
}

export function selectActiveSlide(state: WorkbenchState): SlideDocument | null {
  const deck = lastKnownGood(state.deck);
  if (!deck || !state.activeSlideId) return null;
  return deck.slides.find((slide) => slide.id === state.activeSlideId) ?? null;
}

export function selectInspectorNode(state: WorkbenchState, elementId: ElementId): BodyNode | null {
  return selectActiveSlide(state)?.nodes.find((node) => node.id === elementId) ?? null;
}
