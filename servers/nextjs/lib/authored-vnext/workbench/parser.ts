import type {
  AssetId,
  BodyNode,
  Bounds,
  CasPin,
  DeckDocument,
  DeckId,
  Digest,
  ElementId,
  ElementEditability,
  ImageBodyNode,
  LayoutApplyOperation,
  MasterId,
  NativeChartType,
  OperationId,
  SlideDocument,
  SlideId,
  SceneAssetReference,
  SceneDataReference,
  SceneDataPayload,
  SlideHybridExportPlan,
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

function signedInteger(value: unknown, path: string): number {
  if (!Number.isSafeInteger(value)) {
    throw new WorkbenchParseError(path, "safe integer required");
  }
  return Number(value);
}

function prefixed<T extends string>(value: unknown, prefix: string, path: string): T {
  const parsed = nonEmptyString(value, path);
  if (!parsed.startsWith(prefix) || parsed.length === prefix.length) {
    throw new WorkbenchParseError(path, `${prefix} identifier required`);
  }
  return parsed as T;
}

function elementIdentifier(value: unknown, path: string): ElementId {
  const parsed = nonEmptyString(value, path);
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(parsed)) {
    throw new WorkbenchParseError(path, "stable authored QID required");
  }
  return parsed;
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
    sceneDigest: digest(source.sceneDigest, `${path}.sceneDigest`),
    slideRevision: integer(source.slideRevision, `${path}.slideRevision`),
    slideDigest: digest(source.slideDigest, `${path}.slideDigest`),
    masterDigest: digest(source.masterDigest, `${path}.masterDigest`),
  };
}

function editability(value: unknown, path: string): ElementEditability {
  const source = record(value, path);
  const capability = (name: keyof ElementEditability): boolean => {
    if (typeof source[name] !== "boolean") {
      throw new WorkbenchParseError(`${path}.${name}`, "boolean capability required");
    }
    return source[name] as boolean;
  };
  return {
    text: capability("text"),
    geometry: capability("geometry"),
    asset: capability("asset"),
    data: capability("data"),
    // Legacy sealed snapshots predate style admission; they remain explicitly
    // read-only instead of becoming invalid or locally style-editable.
    style: source.style === undefined ? false : capability("style"),
  };
}

function mediaType(value: unknown, path: string): SceneAssetReference["mediaType"] {
  if (value === "image/png" || value === "image/jpeg" || value === "image/svg+xml" || value === "image/webp") return value;
  throw new WorkbenchParseError(path, "supported image media type required");
}

function nativeChartType(value: unknown, path: string): NativeChartType {
  if (
    value === "bar" || value === "column" || value === "line" ||
    value === "area" || value === "pie" || value === "donut"
  ) return value;
  throw new WorkbenchParseError(path, "native authored chart type required");
}

function sceneDataPayload(value: unknown, path: string): SceneDataPayload {
  return record(value, path);
}

function sceneAssetManifest(value: unknown, path: string): readonly SceneAssetReference[] {
  if (!Array.isArray(value)) throw new WorkbenchParseError(path, "array required");
  const entries = value.map((item, index) => {
    const source = record(item, `${path}[${index}]`);
    return {
      assetId: prefixed<AssetId>(source.assetId, "asset:", `${path}[${index}].assetId`),
      digest: digest(source.assetDigest, `${path}[${index}].assetDigest`),
      mediaType: mediaType(source.mediaType, `${path}[${index}].mediaType`),
      storageReference: nonEmptyString(source.storageReference, `${path}[${index}].storageReference`),
      sizeBytes: integer(source.sizeBytes, `${path}[${index}].sizeBytes`),
    } satisfies SceneAssetReference;
  });
  if (new Set(entries.map((entry) => entry.assetId)).size !== entries.length) throw new WorkbenchParseError(path, "duplicate stable asset id");
  return entries;
}

function sceneDataManifest(value: unknown, path: string): readonly SceneDataReference[] {
  if (!Array.isArray(value)) throw new WorkbenchParseError(path, "array required");
  const entries = value.map((item, index) => {
    const source = record(item, `${path}[${index}]`);
    const kind = source.kind;
    if (kind !== "table" && kind !== "chart") throw new WorkbenchParseError(`${path}[${index}].kind`, "table or chart required");
    return {
      dataId: prefixed<`data:${string}`>(source.dataId, "data:", `${path}[${index}].dataId`),
      digest: digest(source.dataDigest, `${path}[${index}].dataDigest`),
      kind,
      payload: sceneDataPayload(source.payload, `${path}[${index}].payload`),
    } satisfies SceneDataReference;
  });
  if (new Set(entries.map((entry) => entry.dataId)).size !== entries.length) throw new WorkbenchParseError(path, "duplicate stable data id");
  return entries;
}

function canonicalBodyNode(
  value: unknown,
  path: string,
  assets: ReadonlyMap<AssetId, SceneAssetReference> = new Map(),
  data: ReadonlyMap<`data:${string}`, SceneDataReference> = new Map(),
): BodyNode {
  const source = record(value, path);
  const kind = string(source.kind, `${path}.kind`);
  const common = {
    id: elementIdentifier(source.elementId, `${path}.elementId`),
    // DeckSceneIR owns authored body objects only; company furniture remains
    // in the separately pinned master contract. Older in-memory documents may
    // include `layer`, but canonical Scene payloads intentionally do not.
    layer: source.layer === undefined ? "body" : string(source.layer, `${path}.layer`),
    // A canonical canvasBackground intentionally sits behind authored body
    // content, so Scene z-order is a signed ordering key rather than a list
    // index. Keep it exact instead of dropping the background on restore.
    zOrder: signedInteger(source.zOrder, `${path}.zOrder`),
    bounds: bounds(source.bounds, `${path}.bounds`),
    rotation: finite(source.rotation, `${path}.rotation`),
    semanticRole: nonEmptyString(source.semanticRole, `${path}.semanticRole`),
    editability: editability(source.editability, `${path}.editability`),
  };
  if (common.layer !== "body") {
    throw new WorkbenchParseError(path, "only body-layer scene elements are editable");
  }
  if (kind === "text") {
    const role = common.semanticRole;
    const rawStyle = source.style === undefined ? undefined : record(source.style, `${path}.style`);
    const textAlign = rawStyle?.textAlign;
    if (textAlign !== undefined && textAlign !== "left" && textAlign !== "center" && textAlign !== "right" && textAlign !== "justify") throw new WorkbenchParseError(`${path}.style.textAlign`, "supported text alignment required");
    return { ...common, kind, layer: "body", role, text: string(source.text, `${path}.text`), ...(rawStyle ? { style: { ...(typeof rawStyle.color === "string" ? { color: rawStyle.color } : {}), ...(typeof rawStyle.fontFamily === "string" ? { fontFamily: rawStyle.fontFamily } : {}), ...(typeof rawStyle.fontSize === "number" ? { fontSize: finite(rawStyle.fontSize, `${path}.style.fontSize`) } : {}), ...(typeof rawStyle.fontWeight === "number" ? { fontWeight: finite(rawStyle.fontWeight, `${path}.style.fontWeight`) } : {}), ...(textAlign ? { textAlign } : {}) } } : {}) } satisfies TextBodyNode;
  }
  if (kind === "image") {
    const assetId = prefixed<AssetId>(source.assetId, "asset:", `${path}.assetId`);
    const assetDigest = digest(source.assetDigest, `${path}.assetDigest`);
    const manifest = assets.get(assetId);
    if (assets.size > 0 && (!manifest || manifest.digest !== assetDigest)) {
      throw new WorkbenchParseError(path, "image asset must match its sealed asset manifest entry");
    }
    const crop = source.crop === "contain" ? "contain" : source.crop === "cover" ? "cover" : (() => { throw new WorkbenchParseError(`${path}.crop`, "contain or cover required"); })();
    return {
      ...common,
      kind,
      layer: "body",
      assetId,
      digest: assetDigest,
      altText: nonEmptyString(source.altText, `${path}.altText`),
      crop,
      fit: crop,
      ...(manifest ? { mediaType: manifest.mediaType, storageReference: manifest.storageReference, sizeBytes: manifest.sizeBytes } : {}),
      ...(source.cropGeometry === undefined ? {} : { cropGeometry: record(source.cropGeometry, `${path}.cropGeometry`) as ImageBodyNode["cropGeometry"] }),
    } satisfies ImageBodyNode;
  }
  if (kind === "shape") {
    return {
      ...common,
      kind,
      layer: "body",
      shape: nonEmptyString(source.shape, `${path}.shape`),
      ...(source.fill == null ? {} : { fill: nonEmptyString(source.fill, `${path}.fill`) }),
      ...(source.stroke == null ? {} : { stroke: nonEmptyString(source.stroke, `${path}.stroke`) }),
      ...(source.strokeWidth == null ? {} : { strokeWidth: finite(source.strokeWidth, `${path}.strokeWidth`) }),
    };
  }
  if (kind === "connector") {
    return {
      ...common,
      kind,
      layer: "body",
      fromElementId: elementIdentifier(source.fromElementId, `${path}.fromElementId`),
      toElementId: elementIdentifier(source.toElementId, `${path}.toElementId`),
    };
  }
  if (kind === "table") {
    const dataId = prefixed<`data:${string}`>(source.dataId, "data:", `${path}.dataId`);
    const dataDigest = digest(source.dataDigest, `${path}.dataDigest`);
    const manifest = data.get(dataId);
    if (data.size > 0 && (!manifest || manifest.digest !== dataDigest || manifest.kind !== "table")) throw new WorkbenchParseError(path, "table data must match its sealed data manifest entry");
    return {
      ...common,
      kind,
      layer: "body",
      dataId,
      dataDigest,
      rows: integer(source.rows, `${path}.rows`),
      columns: integer(source.columns, `${path}.columns`),
      ...(manifest ? { dataPayload: manifest.payload } : {}),
    };
  }
  if (kind === "chart") {
    const dataId = prefixed<`data:${string}`>(source.dataId, "data:", `${path}.dataId`);
    const dataDigest = digest(source.dataDigest, `${path}.dataDigest`);
    const manifest = data.get(dataId);
    if (data.size > 0 && (!manifest || manifest.digest !== dataDigest || manifest.kind !== "chart")) throw new WorkbenchParseError(path, "chart data must match its sealed data manifest entry");
    return {
      ...common,
      kind,
      layer: "body",
      dataId,
      dataDigest,
      chartType: nativeChartType(source.chartType, `${path}.chartType`),
      ...(source.accessibilitySummary === undefined ? {} : { accessibilitySummary: nonEmptyString(source.accessibilitySummary, `${path}.accessibilitySummary`) }),
      ...(manifest ? { dataPayload: manifest.payload } : {}),
    };
  }
  if (kind === "group") {
    if (!Array.isArray(source.childElementIds) || source.childElementIds.length === 0) {
      throw new WorkbenchParseError(`${path}.childElementIds`, "non-empty array required");
    }
    const childElementIds = source.childElementIds.map((item, index) => elementIdentifier(item, `${path}.childElementIds[${index}]`));
    if (new Set(childElementIds).size !== childElementIds.length) {
      throw new WorkbenchParseError(`${path}.childElementIds`, "duplicate stable element id");
    }
    return { ...common, kind, layer: "body", childElementIds };
  }
  throw new WorkbenchParseError(`${path}.kind`, "unsupported Scene IR element kind");
}

/**
 * Hybrid ownership is a server-sealed receipt.  Keep validation alongside the
 * canonical Scene parser so a malformed payload can never make a raster
 * island and a native object appear to own the same QID in the Workbench.
 */
function hybridExportPlan(
  value: unknown,
  path: string,
  elementIds: ReadonlySet<ElementId>,
): SlideHybridExportPlan {
  const source = record(value, path);
  if (source.schemaVersion !== "slide-hybrid-export-plan/v1") {
    throw new WorkbenchParseError(`${path}.schemaVersion`, "slide-hybrid-export-plan/v1 required");
  }
  if (!Array.isArray(source.ownerDecisions) || source.ownerDecisions.length === 0) {
    throw new WorkbenchParseError(`${path}.ownerDecisions`, "non-empty array required");
  }
  const ownerDecisions = source.ownerDecisions.map((item, index) => {
    const entry = record(item, `${path}.ownerDecisions[${index}]`);
    const decision = entry.decision;
    if (decision !== "nativeEditable" && decision !== "rasterIsland" && decision !== "publicationBlocked") {
      throw new WorkbenchParseError(`${path}.ownerDecisions[${index}].decision`, "sealed hybrid owner decision required");
    }
    const reasonCode = entry.reasonCode === undefined
      ? undefined
      : nonEmptyString(entry.reasonCode, `${path}.ownerDecisions[${index}].reasonCode`);
    if (decision !== "nativeEditable" && !reasonCode) {
      throw new WorkbenchParseError(`${path}.ownerDecisions[${index}].reasonCode`, "non-native owner requires reason");
    }
    return {
      ownerId: elementIdentifier(entry.ownerId, `${path}.ownerDecisions[${index}].ownerId`),
      decision,
      capabilityId: nonEmptyString(entry.capabilityId, `${path}.ownerDecisions[${index}].capabilityId`),
      ...(reasonCode ? { reasonCode } : {}),
    } as const;
  });
  const ownerIds = new Set(ownerDecisions.map((entry) => entry.ownerId));
  if (ownerIds.size !== ownerDecisions.length || ownerIds.size !== elementIds.size || [...ownerIds].some((id) => !elementIds.has(id))) {
    throw new WorkbenchParseError(`${path}.ownerDecisions`, "must seal every Scene QID exactly once");
  }
  if (!Array.isArray(source.rasterIslands)) throw new WorkbenchParseError(`${path}.rasterIslands`, "array required");
  const rasterIslands = source.rasterIslands.map((item, index) => {
    const entry = record(item, `${path}.rasterIslands[${index}]`);
    if (!Array.isArray(entry.sourceOwnerIds) || entry.sourceOwnerIds.length === 0) {
      throw new WorkbenchParseError(`${path}.rasterIslands[${index}].sourceOwnerIds`, "non-empty array required");
    }
    const zBand = record(entry.zBand, `${path}.rasterIslands[${index}].zBand`);
    const minZ = signedInteger(zBand.minZ, `${path}.rasterIslands[${index}].zBand.minZ`);
    const maxZ = signedInteger(zBand.maxZ, `${path}.rasterIslands[${index}].zBand.maxZ`);
    if (minZ > maxZ) throw new WorkbenchParseError(`${path}.rasterIslands[${index}].zBand`, "ordered z band required");
    return {
      islandId: prefixed<`island:${string}`>(entry.islandId, "island:", `${path}.rasterIslands[${index}].islandId`),
      sourceOwnerIds: entry.sourceOwnerIds.map((owner, ownerIndex) => elementIdentifier(owner, `${path}.rasterIslands[${index}].sourceOwnerIds[${ownerIndex}]`)),
      assetId: prefixed<AssetId>(entry.assetId, "asset:", `${path}.rasterIslands[${index}].assetId`),
      assetDigest: digest(entry.assetDigest, `${path}.rasterIslands[${index}].assetDigest`),
      zBand: { minZ, maxZ },
      rasterPlaneDigest: digest(entry.rasterPlaneDigest, `${path}.rasterIslands[${index}].rasterPlaneDigest`),
    } as const;
  });
  const rasterOwners = rasterIslands.flatMap((island) => island.sourceOwnerIds);
  const expectedRasterOwners = ownerDecisions.filter((entry) => entry.decision === "rasterIsland").map((entry) => entry.ownerId);
  if (new Set(rasterIslands.map((island) => island.islandId)).size !== rasterIslands.length || new Set(rasterOwners).size !== rasterOwners.length || rasterOwners.length !== expectedRasterOwners.length || rasterOwners.some((id) => !expectedRasterOwners.includes(id))) {
    throw new WorkbenchParseError(path, "raster islands must be disjoint and match raster owner decisions exactly");
  }
  return { schemaVersion: "slide-hybrid-export-plan/v1", ownerDecisions, rasterIslands };
}

function canonicalSlide(
  value: unknown,
  path: string,
  assets: ReadonlyMap<AssetId, SceneAssetReference> = new Map(),
  data: ReadonlyMap<`data:${string}`, SceneDataReference> = new Map(),
): SlideDocument {
  const source = record(value, path);
  if (!Array.isArray(source.elements)) throw new WorkbenchParseError(`${path}.elements`, "array required");
  const nodes = source.elements.map((node, index) => canonicalBodyNode(node, `${path}.elements[${index}]`, assets, data));
  const ids = new Set(nodes.map((node) => node.id));
  if (ids.size !== nodes.length) throw new WorkbenchParseError(`${path}.nodes`, "duplicate stable element id");
  return {
    id: prefixed<SlideId>(source.slideId, "slide:", `${path}.slideId`),
    revision: integer(source.revision, `${path}.revision`),
    digest: digest(source.slideDigest ?? source.digest, `${path}.slideDigest`),
    layoutId: typeof source.layoutId === "string" && source.layoutId.trim() ? source.layoutId : "layout:scene",
    nodes,
    ...(source.hybridExport === undefined ? {} : { hybridExport: hybridExportPlan(source.hybridExport, `${path}.hybridExport`, ids) }),
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
  const assetManifest = source.assetManifest === undefined ? [] : sceneAssetManifest(source.assetManifest, "deck.assetManifest");
  const dataManifest = source.dataManifest === undefined ? [] : sceneDataManifest(source.dataManifest, "deck.dataManifest");
  const assets = new Map(assetManifest.map((entry) => [entry.assetId, entry]));
  const data = new Map(dataManifest.map((entry) => [entry.dataId, entry]));
  const parsedSlides = source.slides.map((item, index) => canonicalSlide(item, `deck.slides[${index}]`, assets, data));
  if (parsedSlides.length === 0) throw new WorkbenchParseError("deck.slides", "at least one slide required");
  const ids = new Set(parsedSlides.map((item) => item.id));
  if (ids.size !== parsedSlides.length) throw new WorkbenchParseError("deck.slides", "duplicate stable slide id");
  return {
    schemaVersion: "authored-workbench/v1",
    id: prefixed<DeckId>(source.id, "deck:", "deck.id"),
    revision: integer(source.revision, "deck.revision"),
    digest: digest(source.digest, "deck.digest"),
    sceneDigest: digest(source.sceneDigest ?? source.digest, "deck.sceneDigest"),
    assetManifest,
    dataManifest,
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
    zOrder: role === "title" ? 1 : 2,
    role,
    text,
    bounds: nodeBounds,
    rotation: 0,
    semanticRole: role,
    editability: { text: true, geometry: true, asset: false, data: false, style: true },
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
  if (source.scene !== undefined) {
    const scene = record(source.scene, "snapshot.scene");
    if (scene.schemaVersion !== "deck-scene-ir/v1") {
      throw new WorkbenchParseError("snapshot.scene.schemaVersion", "deck-scene-ir/v1 required");
    }
    if (!Array.isArray(scene.slides) || scene.slides.length === 0) {
      throw new WorkbenchParseError("snapshot.scene.slides", "non-empty array required");
    }
    const master = record(source.master, "snapshot.master");
    const assetManifest = sceneAssetManifest(scene.assetManifest, "snapshot.scene.assetManifest");
    const dataManifest = sceneDataManifest(scene.dataManifest, "snapshot.scene.dataManifest");
    const assets = new Map(assetManifest.map((entry) => [entry.assetId, entry]));
    const data = new Map(dataManifest.map((entry) => [entry.dataId, entry]));
    const slides = scene.slides.map((item, index) => canonicalSlide(item, `snapshot.scene.slides[${index}]`, assets, data));
    const ids = new Set(slides.map((item) => item.id));
    if (ids.size !== slides.length) throw new WorkbenchParseError("snapshot.scene.slides", "duplicate stable slide id");
    return {
      schemaVersion: "authored-workbench/v1",
      id: prefixed<DeckId>(scene.deckId, "deck:", "snapshot.scene.deckId"),
      revision: integer(scene.revision, "snapshot.scene.revision"),
      // Deck CAS and Scene identity are separate pins. Both must survive every
      // Workbench save; using sceneDigest as the deck CAS silently weakens it.
      digest: digest(source.digest, "snapshot.digest"),
      sceneDigest: digest(scene.sceneDigest ?? scene.digest, "snapshot.scene.sceneDigest"),
      assetManifest,
      dataManifest,
      master: {
        id: prefixed<MasterId>(master.masterId, "master:", "snapshot.master.masterId"),
        revision: integer(master.masterRevision, "snapshot.master.masterRevision"),
        digest: digest(master.masterDigest, "snapshot.master.masterDigest"),
        layer: "master",
        locked: true,
      },
      slides,
    };
  }
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
      digest: digest(
        generation
          ? rawSlide.specDigest
          : rawSlide.slideDigest ?? rawSlide.digest ?? source.digest,
        `${path}.digest`,
      ),
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
      sceneDigest: digest(source.sceneDigest ?? source.digest, "snapshot.sceneDigest"),
      assetManifest: [],
      dataManifest: [],
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
  const elementId = elementIdentifier(source.elementId, "operation.elementId");
  if (kind === "text.replace") {
    return { kind, operationId, slideId, base, elementId, text: string(source.text, "operation.text") };
  }
  if (kind === "geometry.update") {
    return { kind, operationId, slideId, base, elementId, bounds: bounds(source.bounds, "operation.bounds") };
  }
  if (kind === "asset.set") {
    const crop = source.crop === "contain" ? "contain" : source.crop === "cover" ? "cover" : (() => { throw new WorkbenchParseError("operation.crop", "contain or cover required"); })();
    return {
      kind,
      operationId,
      slideId,
      base,
      elementId,
      assetId: prefixed<AssetId>(source.assetId, "asset:", "operation.assetId"),
      digest: digest(source.digest, "operation.digest"),
      altText: nonEmptyString(source.altText, "operation.altText"),
      crop,
      ...(source.mediaType === undefined ? {} : { mediaType: mediaType(source.mediaType, "operation.mediaType") }),
      ...(source.storageReference === undefined ? {} : { storageReference: nonEmptyString(source.storageReference, "operation.storageReference") }),
      sizeBytes: integer(source.sizeBytes, "operation.sizeBytes"),
    };
  }
  if (kind === "data.set") {
    const dataPayload = sceneDataPayload(source.dataPayload, "operation.dataPayload");
    return {
      kind,
      operationId,
      slideId,
      base,
      elementId,
      dataId: prefixed<`data:${string}`>(source.dataId, "data:", "operation.dataId"),
      dataDigest: digest(source.dataDigest, "operation.dataDigest"),
      dataPayload,
      ...(source.rows === undefined ? {} : { rows: integer(source.rows, "operation.rows") }),
      ...(source.columns === undefined ? {} : { columns: integer(source.columns, "operation.columns") }),
      ...(source.chartType === undefined ? {} : { chartType: nativeChartType(source.chartType, "operation.chartType") }),
      ...(source.accessibilitySummary === undefined ? {} : { accessibilitySummary: nonEmptyString(source.accessibilitySummary, "operation.accessibilitySummary") }),
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
