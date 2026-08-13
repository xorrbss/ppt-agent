import type {
  AssetBodyNode,
  AssetId,
  BodyNode,
  Bounds,
  CasPin,
  DeckDocument,
  DeckId,
  Digest,
  ElementId,
  LayoutApplyOperation,
  MasterId,
  OperationId,
  SlideDocument,
  SlideId,
  TextBodyNode,
  WorkbenchOperation,
} from "./model.ts";

export class WorkbenchParseError extends Error {
  readonly path: string;

  constructor(path: string, message: string) {
    super(`${path}: ${message}`);
    this.name = "WorkbenchParseError";
    this.path = path;
  }
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new WorkbenchParseError(path, "object required");
  }
  return value as Record<string, unknown>;
}

function string(value: unknown, path: string): string {
  if (typeof value !== "string") throw new WorkbenchParseError(path, "string required");
  return value;
}

function nonEmptyString(value: unknown, path: string): string {
  const parsed = string(value, path);
  if (!parsed.trim()) throw new WorkbenchParseError(path, "non-empty string required");
  return parsed;
}

function integer(value: unknown, path: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) {
    throw new WorkbenchParseError(path, "positive safe integer required");
  }
  return Number(value);
}

function finite(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new WorkbenchParseError(path, "finite number required");
  }
  return value;
}

function prefixed<T extends string>(value: unknown, prefix: string, path: string): T {
  const parsed = nonEmptyString(value, path);
  if (!parsed.startsWith(prefix) || parsed.length === prefix.length) {
    throw new WorkbenchParseError(path, `${prefix} identifier required`);
  }
  return parsed as T;
}

function digest(value: unknown, path: string): Digest {
  const parsed = string(value, path);
  if (!/^sha256:[0-9a-f]{64}$/.test(parsed)) {
    throw new WorkbenchParseError(path, "sha256 digest required");
  }
  return parsed as Digest;
}

function bounds(value: unknown, path: string): Bounds {
  const source = record(value, path);
  const parsed = {
    x: finite(source.x, `${path}.x`),
    y: finite(source.y, `${path}.y`),
    width: finite(source.width, `${path}.width`),
    height: finite(source.height, `${path}.height`),
  };
  if (parsed.width <= 0 || parsed.height <= 0) {
    throw new WorkbenchParseError(path, "positive dimensions required");
  }
  return parsed;
}

function casPin(value: unknown, path: string): CasPin {
  const source = record(value, path);
  return {
    deckRevision: integer(source.deckRevision, `${path}.deckRevision`),
    deckDigest: digest(source.deckDigest, `${path}.deckDigest`),
    slideRevision: integer(source.slideRevision, `${path}.slideRevision`),
    masterDigest: digest(source.masterDigest, `${path}.masterDigest`),
  };
}

function bodyNode(value: unknown, path: string): BodyNode {
  const source = record(value, path);
  const kind = string(source.kind, `${path}.kind`);
  const common = {
    id: prefixed<ElementId>(source.id, "element:", `${path}.id`),
    layer: string(source.layer, `${path}.layer`),
    editable: source.editable,
    bounds: bounds(source.bounds, `${path}.bounds`),
  };
  if (common.layer !== "body" || common.editable !== true) {
    throw new WorkbenchParseError(path, "only editable body nodes are canonical");
  }
  if (kind === "text") {
    const role = string(source.role, `${path}.role`);
    if (role !== "title" && role !== "body" && role !== "caption" && role !== "source") {
      throw new WorkbenchParseError(`${path}.role`, "unsupported text role");
    }
    return { ...common, kind, layer: "body", editable: true, role, text: string(source.text, `${path}.text`) } satisfies TextBodyNode;
  }
  if (kind === "asset") {
    return {
      ...common,
      kind,
      layer: "body",
      editable: true,
      assetId: prefixed<AssetId>(source.assetId, "asset:", `${path}.assetId`),
      digest: digest(source.digest, `${path}.digest`),
      altText: nonEmptyString(source.altText, `${path}.altText`),
    } satisfies AssetBodyNode;
  }
  throw new WorkbenchParseError(`${path}.kind`, "unsupported node kind");
}

function slide(value: unknown, path: string): SlideDocument {
  const source = record(value, path);
  if (!Array.isArray(source.nodes)) throw new WorkbenchParseError(`${path}.nodes`, "array required");
  const nodes = source.nodes.map((node, index) => bodyNode(node, `${path}.nodes[${index}]`));
  const ids = new Set(nodes.map((node) => node.id));
  if (ids.size !== nodes.length) throw new WorkbenchParseError(`${path}.nodes`, "duplicate stable element id");
  return {
    id: prefixed<SlideId>(source.id, "slide:", `${path}.id`),
    revision: integer(source.revision, `${path}.revision`),
    layoutId: nonEmptyString(source.layoutId, `${path}.layoutId`),
    nodes,
  };
}

export function parseDeckDocument(value: unknown): DeckDocument {
  const source = record(value, "deck");
  if (source.schemaVersion !== "authored-workbench/v1") {
    throw new WorkbenchParseError("deck.schemaVersion", "authored-workbench/v1 required");
  }
  const master = record(source.master, "deck.master");
  if (master.layer !== "master" || master.locked !== true) {
    throw new WorkbenchParseError("deck.master", "master layer must be locked");
  }
  if (!Array.isArray(source.slides)) throw new WorkbenchParseError("deck.slides", "array required");
  const parsedSlides = source.slides.map((item, index) => slide(item, `deck.slides[${index}]`));
  if (parsedSlides.length === 0) throw new WorkbenchParseError("deck.slides", "at least one slide required");
  const ids = new Set(parsedSlides.map((item) => item.id));
  if (ids.size !== parsedSlides.length) throw new WorkbenchParseError("deck.slides", "duplicate stable slide id");
  return {
    schemaVersion: "authored-workbench/v1",
    id: prefixed<DeckId>(source.id, "deck:", "deck.id"),
    revision: integer(source.revision, "deck.revision"),
    digest: digest(source.digest, "deck.digest"),
    master: {
      id: prefixed<MasterId>(master.id, "master:", "deck.master.id"),
      revision: integer(master.revision, "deck.master.revision"),
      digest: digest(master.digest, "deck.master.digest"),
      layer: "master",
      locked: true,
    },
    slides: parsedSlides,
  };
}

const ADAPTED_BOUNDS = {
  standard: {
    title: { x: 80, y: 70, width: 1120, height: 96 },
    body: { x: 80, y: 190, width: 1120, height: 430 },
  },
  longTitle: {
    title: { x: 80, y: 60, width: 1120, height: 150 },
    body: { x: 80, y: 230, width: 1120, height: 390 },
  },
} as const;

function adaptedTextNode(
  slideId: SlideId,
  role: "title" | "body",
  text: string,
  nodeBounds: Bounds,
): TextBodyNode {
  const suffix = slideId.slice("slide:".length);
  return {
    id: `element:${suffix}:${role}`,
    kind: "text",
    layer: "body",
    editable: true,
    role,
    text,
    bounds: nodeBounds,
  };
}

/**
 * Converts the FastAPI `DeckSnapshot` wire shape into the workbench canonical
 * document. The adapter intentionally derives body node ids instead of using
 * array positions, and never carries renderer HTML into editable state.
 */
interface MasterGeometryContract {
  layouts: Record<string, { id: string; slots: Array<{ role: string; bounds: Bounds }> }>;
}

function layoutBounds(
  layoutId: string,
  contract?: MasterGeometryContract,
): { title: Bounds; body: Bounds } {
  const layout = contract
    ? Object.values(contract.layouts).find((item) => item.id === layoutId)
    : undefined;
  const title = layout?.slots.find((slot) => slot.role === "title")?.bounds;
  const body = layout?.slots.find((slot) => slot.role === "body" || slot.role === "content")?.bounds;
  if (title && body) return { title, body };
  return layoutId.toLowerCase().includes("long-title") || layoutId.toLowerCase().includes("longtitle")
    ? ADAPTED_BOUNDS.longTitle
    : ADAPTED_BOUNDS.standard;
}

export function adaptDeckSnapshotToWorkbenchDocument(
  value: unknown,
  contract?: MasterGeometryContract,
): DeckDocument {
  const source = record(value, "snapshot");
  const generation = source.schemaVersion === "deck-generation-snapshot/v1";
  if (!generation && source.schemaVersion !== "deck-document/v1") {
    throw new WorkbenchParseError("snapshot.schemaVersion", "canonical deck snapshot required");
  }
  if (!Array.isArray(source.slides) || source.slides.length === 0) {
    throw new WorkbenchParseError("snapshot.slides", "non-empty array required");
  }
  const adaptedSlides = source.slides.map((item, index): SlideDocument => {
    const path = `snapshot.slides[${index}]`;
    const envelope = record(item, path);
    const rawSlide = generation ? record(envelope.slide, `${path}.slide`) : envelope;
    const adaptedSlideId = prefixed<SlideId>(rawSlide.slideId, "slide:", `${path}.slideId`);
    const layoutId = nonEmptyString(rawSlide.layoutId, `${path}.layoutId`);
    const title = record(rawSlide.title, `${path}.title`);
    const titleText = string(title.text, `${path}.title.text`);
    const bodyText = string(rawSlide.body, `${path}.body`);
    const resolvedBounds = layoutBounds(layoutId, contract);
    return {
      id: adaptedSlideId,
      revision: integer(rawSlide.revision, `${path}.revision`),
      layoutId,
      nodes: [
        adaptedTextNode(adaptedSlideId, "title", titleText, resolvedBounds.title),
        adaptedTextNode(adaptedSlideId, "body", bodyText, resolvedBounds.body),
      ],
    };
  });
  const slideIds = new Set(adaptedSlides.map((item) => item.id));
  if (slideIds.size !== adaptedSlides.length) {
    throw new WorkbenchParseError("snapshot.slides", "duplicate stable slide id");
  }
  const master = generation ? record(source.master, "snapshot.master") : source;
  return {
    schemaVersion: "authored-workbench/v1",
    id: prefixed<DeckId>(source.deckId, "deck:", "snapshot.deckId"),
    revision: integer(source.revision, "snapshot.revision"),
    digest: digest(source.digest, "snapshot.digest"),
    master: {
      id: prefixed<MasterId>(master.masterId, "master:", "snapshot.masterId"),
      revision: integer(master.masterRevision, "snapshot.masterRevision"),
      digest: digest(master.masterDigest, "snapshot.masterDigest"),
      layer: "master",
      locked: true,
    },
    slides: adaptedSlides,
  };
}

export function parseWorkbenchOperation(value: unknown): WorkbenchOperation {
  const source = record(value, "operation");
  const operationId = prefixed<OperationId>(source.operationId, "op:", "operation.operationId");
  const slideId = prefixed<SlideId>(source.slideId, "slide:", "operation.slideId");
  const base = casPin(source.base, "operation.base");
  const kind = string(source.kind, "operation.kind");
  if (kind === "layout.apply") {
    return { kind, operationId, slideId, base, layoutId: nonEmptyString(source.layoutId, "operation.layoutId") } satisfies LayoutApplyOperation;
  }
  const elementId = prefixed<ElementId>(source.elementId, "element:", "operation.elementId");
  if (kind === "text.replace") {
    return { kind, operationId, slideId, base, elementId, text: string(source.text, "operation.text") };
  }
  if (kind === "geometry.update") {
    return { kind, operationId, slideId, base, elementId, bounds: bounds(source.bounds, "operation.bounds") };
  }
  if (kind === "asset.set") {
    return {
      kind,
      operationId,
      slideId,
      base,
      elementId,
      assetId: prefixed<AssetId>(source.assetId, "asset:", "operation.assetId"),
      digest: digest(source.digest, "operation.digest"),
      altText: nonEmptyString(source.altText, "operation.altText"),
    };
  }
  throw new WorkbenchParseError("operation.kind", "unsupported typed operation");
}

export function isWorkbenchOperation(value: unknown): value is WorkbenchOperation {
  try {
    parseWorkbenchOperation(value);
    return true;
  } catch {
    return false;
  }
}
