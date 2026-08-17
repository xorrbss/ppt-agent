import type {
  AIEditZone,
  Bounds,
  BrandAsset,
  DeckMasterContractV1,
  LayoutKind,
  MasterCustomLayout,
  MasterImageCrop,
  MasterFurniture,
  MasterLayout,
  MasterSlot,
  Ownership,
  SemanticMasterElement,
  SemanticMasterElementContent,
  SemanticMasterElementStyle,
  SemanticMasterElementScope,
  SemanticMasterElementVisibility,
} from "../../../../lib/authored-vnext/master/contracts.ts";

export type MasterCanvasElementKind =
  | "text"
  | "image"
  | "line"
  | "rect"
  | "shape"
  | "pageNumber"
  | "placeholder"
  | "aiEditZone";

export interface MasterCanvasPlaceholder {
  role: MasterSlot["role"];
  placeholderIndex: number;
  style: MasterSlot["style"];
}

export interface MasterCanvasElement {
  id: string;
  kind: MasterCanvasElementKind;
  layer: "furniture" | "slot" | "semantic" | "aiEditZone";
  role: string;
  bounds: Bounds;
  rotation: number;
  visible: boolean;
  visibility: MasterFurniture["visibility"] | "layoutOnly" | "hidden";
  ownership: Ownership;
  /** Human master authors may edit locked furniture; ownership is an AI boundary. */
  editable: boolean;
  deletable: boolean;
  duplicable: boolean;
  rotatable: boolean;
  zIndex: number;
  text?: string;
  asset?: BrandAsset | null;
  fill?: string;
  stroke?: string;
  placeholder?: MasterCanvasPlaceholder;
  fontFamily: string;
  fontSize: number;
  fontWeight: number;
  color: string;
  style?: SemanticMasterElementStyle;
  content?: SemanticMasterElementContent;
  editableByUser?: boolean;
  editableByAI?: boolean;
  locked?: boolean;
  /** Preserves zone intent for the canvas label and property inspector. */
  aiEditZone?: AIEditZone;
}

export interface MasterCanvasScene {
  width: number;
  height: number;
  background: string;
  safeArea: Bounds;
  colors: DeckMasterContractV1["tokens"]["colors"];
  layoutKind: string;
  layoutId: string;
  elements: MasterCanvasElement[];
}

export type MasterCanvasClipboardItem =
  | { kind: "semantic"; element: SemanticMasterElement }
  | { kind: "slot"; slot: MasterSlot };

export type MasterCanvasAlignment =
  | "left"
  | "center"
  | "right"
  | "top"
  | "middle"
  | "bottom";

export type SharedLogoAnchor =
  | "topLeft"
  | "topRight"
  | "bottomLeft"
  | "bottomRight";

export type AIEditZonePatch = {
  scope?: AIEditZone["scope"] | null;
  instruction?: AIEditZone["instruction"] | null;
  contentType?: AIEditZone["contentType"] | null;
  pattern?: AIEditZone["pattern"] | null;
  sourcePolicy?: AIEditZone["sourcePolicy"] | null;
  emptyBehavior?: AIEditZone["emptyBehavior"] | null;
  fallbackText?: AIEditZone["fallbackText"] | null;
  maxChars?: AIEditZone["maxChars"] | null;
  maxItems?: AIEditZone["maxItems"] | null;
  density?: AIEditZone["density"] | null;
  textAlign?: AIEditZone["textAlign"] | null;
  stylePolicy?: AIEditZone["stylePolicy"] | null;
};

export type MasterCanvasTransaction =
  | { type: "insertElement"; element: SemanticMasterElement }
  | {
      /** One undoable insertion materializes a shared brand logo per layout. */
      type: "insertSharedLogo";
      asset: BrandAsset;
      layoutIds: string[];
      anchor: SharedLogoAnchor;
      /** Supplying this makes replay/test fixtures deterministic. */
      groupId?: string;
    }
  | { type: "setSharedLogoOverride"; elementId: string }
  | { type: "restoreSharedLogo"; elementId: string }
  | {
      type: "updateGeometry";
      elementId: string;
      bounds: Bounds;
      rotation?: number;
    }
  | {
      type: "updateGeometryBatch";
      updates: Array<{
        elementId: string;
        bounds: Bounds;
        rotation?: number;
      }>;
    }
  | { type: "updateText"; elementId: string; text: string }
  | { type: "updateImage"; elementId: string; asset: BrandAsset | null }
  | { type: "updateStyle"; elementIds: string[]; style: Partial<SemanticMasterElementStyle> }
  | { type: "updateContent"; elementId: string; content: Partial<SemanticMasterElementContent> }
  | { type: "updateVisibility"; elementIds: string[]; visibility: SemanticMasterElementVisibility }
  | { type: "updateScope"; elementIds: string[]; scope: SemanticMasterElementScope }
  | {
      type: "updateOwnership";
      elementIds: string[];
      editableByUser?: boolean;
      editableByAI?: boolean;
      locked?: boolean;
    }
  | {
      type: "updateImagePresentation";
      elementId: string;
      asset?: BrandAsset | null;
      assetId?: string;
      digest?: BrandAsset["digest"];
      mediaType?: "image/png" | "image/jpeg" | "image/svg+xml";
      altText?: string;
      fit?: "contain" | "cover";
      crop?: MasterImageCrop | null;
    }
  | { type: "setBackground"; color: string }
  | { type: "addAiEditZone"; zone: AIEditZone }
  | { type: "updateAiEditZoneGeometry"; zoneId: string; bounds: Bounds }
  | {
      type: "updateAiEditZone";
      zoneId: string;
      /** `null` removes an optional field from the persisted canonical zone. */
      patch: AIEditZonePatch;
    }
  | {
      type: "splitAiEditZoneByLayout";
      zoneId: string;
      layoutIds: string[];
      idFactory: (layoutId: string) => string;
    }
  | { type: "deleteAiEditZones"; zoneIds: string[] }
  | {
      type: "align";
      elementIds: string[];
      alignment: MasterCanvasAlignment;
      relativeTo: "selection" | "safeArea";
    }
  | {
      type: "reorder";
      elementIds: string[];
      direction: "front" | "back" | "forward" | "backward";
    }
  | { type: "delete"; elementIds: string[] }
  | {
      type: "duplicate";
      elementIds: string[];
      idFactory?: (sourceId: string, copyNumber: number) => string;
    }
  | {
      type: "paste";
      items: MasterCanvasClipboardItem[];
      idFactory?: (sourceId: string, copyNumber: number) => string;
    }
  | { type: "addLayout"; layout: MasterCustomLayout }
  | { type: "cloneLayout"; layoutId: string; newLayoutId: string; name?: string }
  | { type: "deleteLayout"; layoutId: string };

export class MasterCanvasAdapterError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MasterCanvasAdapterError";
  }
}

const FURNITURE_Z_BASE = 1_000;

function furnitureKind(kind: MasterFurniture["kind"]): MasterCanvasElementKind {
  return kind === "logo" ? "image" : kind;
}

function isFurnitureVisible(
  visibility: MasterFurniture["visibility"],
  layoutKind: LayoutKind,
): boolean {
  if (visibility === "hidden") return false;
  if (visibility === "coverOnly") return layoutKind === "cover";
  if (visibility === "exceptCover") return layoutKind !== "cover";
  return true;
}

function assetForFurniture(
  contract: DeckMasterContractV1,
  item: MasterFurniture,
): BrandAsset | null {
  if (!item.assetId) return null;
  const assets = [contract.brandAssets.logo, contract.brandAssets.wordmark];
  return assets.find((asset) => asset?.assetId === item.assetId) ?? null;
}

function furnitureElement(
  contract: DeckMasterContractV1,
  layoutKind: LayoutKind,
  role: string,
  item: MasterFurniture,
  index: number,
): MasterCanvasElement {
  const asset = item.kind === "logo" ? assetForFurniture(contract, item) : null;
  const isBoundLogo = item.kind === "logo" && asset !== null;
  const isVisibleTextFurniture = item.kind !== "text" || Boolean(item.text?.trim());
  return {
    id: item.id,
    kind: furnitureKind(item.kind),
    layer: "furniture",
    role,
    bounds: { ...item.bounds },
    rotation: 0,
    visible: isFurnitureVisible(item.visibility, layoutKind) &&
      (item.kind !== "logo" || isBoundLogo) && isVisibleTextFurniture,
    visibility: item.visibility,
    ownership: { ...item.ownership },
    editable: true,
    // Fixed text is retained as a structural furniture record and cleared by
    // delete. This preserves the strict furniture namespace while removing the
    // selected header/footer text and makes the history entry reversible.
    deletable: isBoundLogo || (item.kind === "text" && Boolean(item.text?.trim())),
    duplicable: false,
    rotatable: false,
    zIndex: FURNITURE_Z_BASE + index,
    ...(item.text === undefined ? {} : { text: item.text }),
    ...(item.kind === "logo" ? { asset } : {}),
    ...(item.fill === undefined ? {} : { fill: item.fill }),
    ...(item.stroke === undefined ? {} : { stroke: item.stroke }),
    fontFamily: contract.fontManifest.primary,
    fontSize: item.kind === "pageNumber" ? contract.semanticStyles.caption.fontPt : contract.semanticStyles.note.fontPt,
    fontWeight: item.kind === "pageNumber" ? contract.semanticStyles.caption.fontWeight : contract.semanticStyles.note.fontWeight,
    color: item.fill ?? contract.tokens.colors.textSecondary,
  };
}

function slotElement(
  contract: DeckMasterContractV1,
  item: MasterSlot,
  index: number,
): MasterCanvasElement {
  const style = contract.semanticStyles[item.style];
  return {
    id: item.id,
    kind: "placeholder",
    layer: "slot",
    role: item.role,
    bounds: { ...item.bounds },
    rotation: 0,
    visible: true,
    visibility: "layoutOnly",
    ownership: { ...item.ownership },
    editable: true,
    // Built-in layouts must retain title/body slots for strict publish.
    deletable: item.role === "content",
    duplicable: true,
    rotatable: false,
    zIndex: index,
    placeholder: {
      role: item.role,
      placeholderIndex: item.placeholderIndex,
      style: item.style,
    },
    fontFamily: contract.tokens.fonts[style.fontToken],
    fontSize: style.fontPt,
    fontWeight: style.fontWeight,
    color: contract.tokens.colors[style.colorToken],
  };
}

function findLayout(
  contract: DeckMasterContractV1,
  layoutIdOrKind: string,
): { layout: MasterLayout | MasterCustomLayout; builtInKind: LayoutKind | null } {
  if (layoutIdOrKind in contract.layouts) {
    const kind = layoutIdOrKind as LayoutKind;
    return { layout: contract.layouts[kind], builtInKind: kind };
  }
  for (const [kind, layout] of Object.entries(contract.layouts)) {
    if (layout.id === layoutIdOrKind) return { layout, builtInKind: kind as LayoutKind };
  }
  const custom = contract.customLayouts?.find((layout) => layout.id === layoutIdOrKind);
  if (custom) return { layout: custom, builtInKind: null };
  throw new MasterCanvasAdapterError(`Unknown layout: ${layoutIdOrKind}`);
}

function semanticElementVisible(
  element: SemanticMasterElement,
  layoutId: string,
  builtInKind: LayoutKind | null,
): boolean {
  if (element.visibility === "hidden") return false;
  if (element.scope.kind === "layout" && element.scope.layoutId !== layoutId) return false;
  if (element.visibility === "coverOnly") return builtInKind === "cover";
  if (element.visibility === "exceptCover") return builtInKind !== "cover";
  if (element.visibility === "layoutOnly") return element.scope.kind === "layout";
  return true;
}

function isLegacyContentSurface(element: SemanticMasterElement): boolean {
  return element.type === "placeholder" ||
    Boolean(element.content.placeholderRole) ||
    element.content.role === "ai-text-zone";
}

function isConfirmedFurnitureSemantic(element: SemanticMasterElement): boolean {
  if (isLegacyContentSurface(element)) return false;
  const role = element.content.role;
  return (role === "brand-logo" && element.type === "image") ||
    (role === "slide-number" && element.type === "pageNumber") ||
    (["fixed-copy", "header", "footer"].includes(role ?? "") && element.type === "text") ||
    (role === "divider" && element.type === "line") ||
    (role === "background-decoration" && element.type === "shape");
}

function assertConfirmedFurnitureSemantic(
  contract: DeckMasterContractV1,
  elementId: string,
): SemanticMasterElement {
  const element = semanticElementEntry(contract, elementId);
  if (!element || !isConfirmedFurnitureSemantic(element)) {
    throw new MasterCanvasAdapterError("Only confirmed master furniture can be edited in furniture setup");
  }
  return element;
}

function aiEditZoneElement(zone: AIEditZone, zIndex: number): MasterCanvasElement {
  return {
    id: zone.id,
    kind: "aiEditZone",
    layer: "aiEditZone",
    role: "ai-edit-envelope",
    bounds: { ...zone.bounds },
    rotation: 0,
    visible: true,
    visibility: "layoutOnly",
    ownership: { locked: false, modelWritable: true },
    editable: true,
    deletable: true,
    duplicable: false,
    rotatable: false,
    zIndex,
    fontFamily: "",
    fontSize: 0,
    fontWeight: 0,
    color: "#7c3aed",
    aiEditZone: { ...zone, scope: { ...zone.scope }, bounds: { ...zone.bounds } },
  };
}

function semanticCanvasElement(
  contract: DeckMasterContractV1,
  element: SemanticMasterElement,
  layoutId: string,
  builtInKind: LayoutKind | null,
): MasterCanvasElement {
  const style = element.style;
  const content = element.content;
  const registeredAsset = content.assetId
    ? [contract.brandAssets.logo, contract.brandAssets.wordmark].find(
        (candidate) => candidate?.assetId === content.assetId,
      ) ?? null
    : null;
  const asset = registeredAsset ?? (content.assetId && content.digest
    ? { assetId: content.assetId, digest: content.digest, altText: content.altText ?? "" }
    : null);
  return {
    id: element.id,
    kind: element.type,
    layer: "semantic",
    role: content.role ?? content.placeholderRole ?? element.type,
    bounds: { ...element.bounds },
    rotation: element.rotation,
    visible: semanticElementVisible(element, layoutId, builtInKind),
    visibility: element.visibility,
    ownership: { locked: element.locked, modelWritable: element.editableByAI },
    editable: element.editableByUser && !element.locked,
    // A common semantic element has no layout-local hide/override contract;
    // deleting it from one canvas would silently remove it everywhere. Shared
    // logos are the one explicit propagated exception.
    deletable: !element.locked &&
      (element.scope.kind === "layout" || Boolean(content.sharedGroupId)),
    duplicable: !element.locked && !Boolean(content.sharedGroupId),
    rotatable: element.type !== "placeholder" && element.type !== "pageNumber",
    zIndex: element.zIndex,
    ...(content.text === undefined ? {} : { text: content.text }),
    ...(element.type === "image" ? { asset } : {}),
    ...(style.fill === undefined ? {} : { fill: style.fill }),
    ...(style.stroke === undefined ? {} : { stroke: style.stroke }),
    ...(element.type === "placeholder" && content.placeholderRole
      ? {
          placeholder: {
            role: content.placeholderRole === "image" ? "content" : content.placeholderRole,
            placeholderIndex: content.placeholderIndex ?? 0,
            style: content.placeholderRole === "title" ? "title" : "body",
          },
        }
      : {}),
    fontFamily: style.fontFamily ?? contract.fontManifest.primary,
    fontSize: style.fontSize ?? contract.semanticStyles.body.fontPt,
    fontWeight: style.fontWeight ?? contract.semanticStyles.body.fontWeight,
    color: style.color ?? contract.tokens.colors.textPrimary,
    style: { ...style },
    content: {
      ...content,
      ...(content.crop ? { crop: { ...content.crop } } : {}),
    },
    editableByUser: element.editableByUser,
    editableByAI: element.editableByAI,
    locked: element.locked,
  };
}

/**
 * Produces transient canvas data from the semantic master. The returned scene
 * deliberately is not serializable canonical state and can always be rebuilt.
 */
export function masterContractToCanvasScene(
  contract: DeckMasterContractV1,
  layoutIdOrKind: string,
): MasterCanvasScene {
  const { layout, builtInKind } = findLayout(contract, layoutIdOrKind);
  const furnitureLayoutKind = builtInKind ?? "standard";
  return {
    width: contract.canvas.widthPx,
    height: contract.canvas.heightPx,
    background: contract.tokens.colors.background,
    safeArea: { ...contract.canvas.safeArea },
    colors: { ...contract.tokens.colors },
    layoutKind: builtInKind ?? layout.id,
    layoutId: layout.id,
    elements: [
      ...Object.entries(contract.furniture).map(([role, item], index) =>
        furnitureElement(contract, furnitureLayoutKind, role, item, index),
      ),
      ...(contract.semanticElements ?? [])
        .filter((element) =>
          semanticElementVisible(element, layout.id, builtInKind) &&
          isConfirmedFurnitureSemantic(element),
        )
        .map((element) => semanticCanvasElement(contract, element, layout.id, builtInKind)),
      ...(contract.aiEditZones ?? [])
        .filter((zone) => zone.scope.kind === "common" || zone.scope.layoutId === layout.id)
        .map((zone, index) => aiEditZoneElement(zone, 40_000 + index)),
    ],
  };
}

function assertBounds(bounds: Bounds): Bounds {
  if (
    !Number.isFinite(bounds.x) ||
    !Number.isFinite(bounds.y) ||
    !Number.isFinite(bounds.width) ||
    !Number.isFinite(bounds.height) ||
    bounds.x < 0 ||
    bounds.y < 0 ||
    bounds.width <= 0 ||
    bounds.height <= 0
  ) {
    throw new MasterCanvasAdapterError("Canvas bounds must be finite and positive");
  }
  return { ...bounds };
}

function furnitureEntry(
  contract: DeckMasterContractV1,
  elementId: string,
): [keyof DeckMasterContractV1["furniture"], MasterFurniture] | null {
  for (const [key, item] of Object.entries(contract.furniture)) {
    if (item.id === elementId) {
      return [key as keyof DeckMasterContractV1["furniture"], item];
    }
  }
  return null;
}

function slotIndex(
  contract: DeckMasterContractV1,
  layoutIdOrKind: string,
  elementId: string,
): number {
  return findLayout(contract, layoutIdOrKind).layout.slots.findIndex((slot) => slot.id === elementId);
}

function assertElementExists(
  contract: DeckMasterContractV1,
  layoutIdOrKind: string,
  elementId: string,
): void {
  if (furnitureEntry(contract, elementId)) return;
  if ((contract.semanticElements ?? []).some((element) => element.id === elementId)) {
    assertConfirmedFurnitureSemantic(contract, elementId);
    return;
  }
  throw new MasterCanvasAdapterError(`Only confirmed furniture can be edited: ${elementId}`);
}

function updateElement(
  contract: DeckMasterContractV1,
  layoutIdOrKind: string,
  elementId: string,
  updateFurniture: (item: MasterFurniture) => MasterFurniture,
  updateSlot: (item: MasterSlot) => MasterSlot,
): DeckMasterContractV1 {
  const entry = furnitureEntry(contract, elementId);
  if (entry) {
    const [key, item] = entry;
    return {
      ...contract,
      furniture: { ...contract.furniture, [key]: updateFurniture(item) },
    };
  }
  const index = slotIndex(contract, layoutIdOrKind, elementId);
  if (index < 0) throw new MasterCanvasAdapterError(`Unknown canvas element: ${elementId}`);
  const { layout, builtInKind } = findLayout(contract, layoutIdOrKind);
  const slots = [...layout.slots];
  slots[index] = updateSlot(slots[index]);
  if (builtInKind) {
    return {
      ...contract,
      layouts: {
        ...contract.layouts,
        [builtInKind]: { ...layout, slots } as MasterLayout,
      },
    };
  }
  return {
    ...contract,
    customLayouts: (contract.customLayouts ?? []).map((candidate) =>
      candidate.id === layout.id ? { ...candidate, slots } : candidate,
    ),
  };
}

function updateBounds(
  contract: DeckMasterContractV1,
  layoutIdOrKind: string,
  elementId: string,
  bounds: Bounds,
): DeckMasterContractV1 {
  const nextBounds = assertBounds(bounds);
  if ((contract.semanticElements ?? []).some((element) => element.id === elementId)) {
    assertConfirmedFurnitureSemantic(contract, elementId);
    return {
      ...contract,
      semanticElements: (contract.semanticElements ?? []).map((element) =>
        element.id === elementId ? { ...element, bounds: nextBounds } : element,
      ),
    };
  }
  if (!furnitureEntry(contract, elementId)) {
    throw new MasterCanvasAdapterError(`Only confirmed furniture can be edited: ${elementId}`);
  }
  return updateElement(
    contract,
    layoutIdOrKind,
    elementId,
    (item) => ({ ...item, bounds: nextBounds }),
    (item) => ({ ...item, bounds: nextBounds }),
  );
}

function selectedEnvelope(elements: MasterCanvasElement[]): Bounds {
  const left = Math.min(...elements.map((item) => item.bounds.x));
  const top = Math.min(...elements.map((item) => item.bounds.y));
  const right = Math.max(...elements.map((item) => item.bounds.x + item.bounds.width));
  const bottom = Math.max(...elements.map((item) => item.bounds.y + item.bounds.height));
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function alignBounds(
  bounds: Bounds,
  target: Bounds,
  selection: Bounds,
  alignment: MasterCanvasAlignment,
  relativeTo: "selection" | "safeArea",
): Bounds {
  const result = { ...bounds };
  if (relativeTo === "safeArea") {
    const dx =
      alignment === "left"
        ? target.x - selection.x
        : alignment === "center"
          ? target.x + target.width / 2 - (selection.x + selection.width / 2)
          : alignment === "right"
            ? target.x + target.width - (selection.x + selection.width)
            : 0;
    const dy =
      alignment === "top"
        ? target.y - selection.y
        : alignment === "middle"
          ? target.y + target.height / 2 - (selection.y + selection.height / 2)
          : alignment === "bottom"
            ? target.y + target.height - (selection.y + selection.height)
            : 0;
    result.x += dx;
    result.y += dy;
    return result;
  }
  if (alignment === "left") result.x = selection.x;
  if (alignment === "center") result.x = selection.x + (selection.width - bounds.width) / 2;
  if (alignment === "right") result.x = selection.x + selection.width - bounds.width;
  if (alignment === "top") result.y = selection.y;
  if (alignment === "middle") result.y = selection.y + (selection.height - bounds.height) / 2;
  if (alignment === "bottom") result.y = selection.y + selection.height - bounds.height;
  return result;
}

function moveSelection(
  slots: MasterSlot[],
  selectedIds: Set<string>,
  direction: "front" | "back" | "forward" | "backward",
): MasterSlot[] {
  if (direction === "front" || direction === "back") {
    const selected = slots.filter((slot) => selectedIds.has(slot.id));
    const rest = slots.filter((slot) => !selectedIds.has(slot.id));
    return direction === "front" ? [...rest, ...selected] : [...selected, ...rest];
  }
  const result = [...slots];
  if (direction === "forward") {
    for (let index = result.length - 2; index >= 0; index -= 1) {
      if (selectedIds.has(result[index].id) && !selectedIds.has(result[index + 1].id)) {
        [result[index], result[index + 1]] = [result[index + 1], result[index]];
      }
    }
  } else {
    for (let index = 1; index < result.length; index += 1) {
      if (selectedIds.has(result[index].id) && !selectedIds.has(result[index - 1].id)) {
        [result[index], result[index - 1]] = [result[index - 1], result[index]];
      }
    }
  }
  return result;
}

function defaultCopyId(sourceId: string, copyNumber: number): string {
  return `${sourceId}-copy${copyNumber === 1 ? "" : `-${copyNumber}`}`;
}

function semanticElementEntry(
  contract: DeckMasterContractV1,
  elementId: string,
): SemanticMasterElement | null {
  return (contract.semanticElements ?? []).find((element) => element.id === elementId) ?? null;
}

function updateSemanticElement(
  contract: DeckMasterContractV1,
  elementId: string,
  updater: (element: SemanticMasterElement) => SemanticMasterElement,
): DeckMasterContractV1 {
  assertConfirmedFurnitureSemantic(contract, elementId);
  return {
    ...contract,
    semanticElements: (contract.semanticElements ?? []).map((element) =>
      element.id === elementId ? updater(element) : element,
    ),
  };
}

function assertCrop(crop: MasterImageCrop): MasterImageCrop {
  if (
    ![crop.x, crop.y, crop.width, crop.height].every(Number.isFinite) ||
    crop.x < 0 || crop.y < 0 || crop.width <= 0 || crop.height <= 0 ||
    crop.x + crop.width > 1 || crop.y + crop.height > 1
  ) {
    throw new MasterCanvasAdapterError("Image crop must stay inside normalized 0..1 bounds");
  }
  return { ...crop };
}

function allCanvasIds(contract: DeckMasterContractV1): Set<string> {
  return new Set([
    ...Object.values(contract.furniture).map((item) => item.id),
    ...Object.values(contract.layouts).flatMap((layout) => layout.slots.map((slot) => slot.id)),
    ...(contract.customLayouts ?? []).flatMap((layout) => layout.slots.map((slot) => slot.id)),
    ...(contract.semanticElements ?? []).map((element) => element.id),
    ...(contract.aiEditZones ?? []).map((zone) => zone.id),
  ]);
}

/** Captures canonical element data so copy survives layout changes and remounts. */
export function masterCanvasClipboardItems(
  contract: DeckMasterContractV1,
  layoutIdOrKind: string,
  elementIds: string[],
): MasterCanvasClipboardItem[] {
  const scene = masterContractToCanvasScene(contract, layoutIdOrKind);
  const { layout } = findLayout(contract, layoutIdOrKind);
  return [...new Set(elementIds)].flatMap((elementId) => {
    const canvasElement = scene.elements.find(
      (candidate) => candidate.id === elementId && candidate.duplicable,
    );
    if (!canvasElement) return [];
    if (canvasElement.layer === "semantic") {
      const element = semanticElementEntry(contract, elementId);
      return element
        ? [{ kind: "semantic" as const, element: validateSemanticElement(contract, element) }]
        : [];
    }
    if (canvasElement.layer === "slot") {
      const slot = layout.slots.find((candidate) => candidate.id === elementId);
      return slot
        ? [{
            kind: "slot" as const,
            slot: {
              ...slot,
              bounds: { ...slot.bounds },
              ownership: { ...slot.ownership },
            },
          }]
        : [];
    }
    return [];
  });
}

function validateAiEditZone(
  contract: DeckMasterContractV1,
  zone: AIEditZone,
): AIEditZone {
  if (!/^ai-zone:[a-z0-9][a-z0-9._-]*$/i.test(zone.id)) {
    throw new MasterCanvasAdapterError(`Invalid AI edit zone ID: ${zone.id}`);
  }
  if (zone.scope.kind === "layout") findLayout(contract, zone.scope.layoutId);
  const normalized: AIEditZone = {
    ...zone,
    id: zone.id,
    scope: { ...zone.scope },
    bounds: assertBounds(zone.bounds),
  };
  if (normalized.instruction !== undefined &&
    (typeof normalized.instruction !== "string" || normalized.instruction.length > 4000)) {
    throw new MasterCanvasAdapterError("AI zone instruction must be at most 4000 characters");
  }
  if (normalized.contentType !== undefined &&
    !["auto", "text", "cards", "list", "table", "chart", "mixed"].includes(normalized.contentType)) {
    throw new MasterCanvasAdapterError("Invalid AI zone content type");
  }
  if (normalized.pattern !== undefined &&
    !["auto", "numberedCards", "bulletList", "comparisonTable", "metricChart", "iconCards"].includes(normalized.pattern)) {
    throw new MasterCanvasAdapterError("Invalid AI zone pattern");
  }
  if (normalized.sourcePolicy !== undefined &&
    !["documentOnly", "promptAndDocument", "promptOnly"].includes(normalized.sourcePolicy)) {
    throw new MasterCanvasAdapterError("Invalid AI zone source policy");
  }
  if (normalized.emptyBehavior !== undefined &&
    !["hidden", "blank", "fallback"].includes(normalized.emptyBehavior)) {
    throw new MasterCanvasAdapterError("Invalid AI zone empty behavior");
  }
  if (normalized.density !== undefined &&
    !["sparse", "balanced", "dense"].includes(normalized.density)) {
    throw new MasterCanvasAdapterError("Invalid AI zone density");
  }
  if (normalized.textAlign !== undefined &&
    !["left", "center", "right"].includes(normalized.textAlign)) {
    throw new MasterCanvasAdapterError("Invalid AI zone text alignment");
  }
  if (normalized.stylePolicy !== undefined && normalized.stylePolicy !== "inheritTemplate") {
    throw new MasterCanvasAdapterError("AI zones must inherit the template style");
  }
  if ([normalized.maxChars, normalized.maxItems].some((value) =>
    value !== undefined && (!Number.isInteger(value) || value <= 0)) ||
    (normalized.maxChars !== undefined && normalized.maxChars > 10000) ||
    (normalized.maxItems !== undefined && normalized.maxItems > 24)) {
    throw new MasterCanvasAdapterError("AI zone limits must be positive and within supported bounds");
  }
  if (normalized.fallbackText !== undefined &&
    (typeof normalized.fallbackText !== "string" || normalized.fallbackText.length > 10000)) {
    throw new MasterCanvasAdapterError("AI zone fallback text must be at most 10000 characters");
  }
  if (normalized.emptyBehavior === "fallback" && !normalized.fallbackText?.trim()) {
    throw new MasterCanvasAdapterError("Fallback AI zones require fallback text");
  }
  if (normalized.fallbackText !== undefined && normalized.emptyBehavior !== "fallback") {
    throw new MasterCanvasAdapterError("AI zone fallback text requires fallback behavior");
  }
  if (normalized.fallbackText !== undefined && normalized.maxChars !== undefined &&
    normalized.fallbackText.length > normalized.maxChars) {
    throw new MasterCanvasAdapterError("AI zone fallback text cannot exceed the maximum character count");
  }
  return normalized;
}

function layoutScopedAiZoneId(
  sourceId: string,
  layoutId: string,
  usedIds: Set<string>,
): string {
  const suffix = layoutId
    .replace(/^layout:/, "")
    .replace(/[^a-z0-9._-]+/gi, "-")
    .replace(/^-+|-+$/g, "") || "layout";
  let candidate = `${sourceId}.${suffix}`;
  let copy = 2;
  while (usedIds.has(candidate)) {
    candidate = `${sourceId}.${suffix}-${copy}`;
    copy += 1;
  }
  usedIds.add(candidate);
  return candidate;
}

/**
 * Upgrades historical shared AI envelopes into layout-local zones while
 * preserving the exact authored geometry.
 * This is intentionally deterministic so the editor state, saved draft and
 * published revision all carry the same canonical contract.
 */
export function normalizeAiEditZonesForLayoutEditing(
  contract: DeckMasterContractV1,
): DeckMasterContractV1 {
  const zones = contract.aiEditZones ?? [];
  if (!zones.length) return contract;
  const layoutIds = [
    ...Object.values(contract.layouts).map((layout) => layout.id),
    ...(contract.customLayouts ?? []).map((layout) => layout.id),
  ];
  const usedIds = allCanvasIds(contract);
  zones
    .filter((zone) => zone.scope.kind === "common")
    .forEach((zone) => usedIds.delete(zone.id));
  let changed = false;
  const normalized = zones.flatMap((zone) => {
    const exactZone = validateAiEditZone(contract, zone);
    if (zone.scope.kind === "layout") return [exactZone];
    changed = true;
    return layoutIds.map((layoutId) =>
      validateAiEditZone(contract, {
        ...exactZone,
        id: layoutScopedAiZoneId(zone.id, layoutId, usedIds),
        scope: { kind: "layout", layoutId },
      }),
    );
  });
  return changed ? { ...contract, aiEditZones: normalized } : contract;
}

function furnitureVisibleOnLayout(
  visibility: MasterFurniture["visibility"],
  layoutKind: LayoutKind | "custom",
): boolean {
  if (visibility === "hidden") return false;
  if (visibility === "coverOnly") return layoutKind === "cover";
  if (visibility === "exceptCover") return layoutKind !== "cover";
  return true;
}

function localFurnitureElementId(
  role: string,
  layoutId: string,
  usedIds: Set<string>,
): string {
  const token = (value: string) => value
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "item";
  const base = `element:layout-furniture.${token(role)}.${token(layoutId.replace(/^layout:/, ""))}`;
  let candidate = base;
  let copy = 2;
  while (usedIds.has(candidate)) {
    candidate = `${base}-${copy}`;
    copy += 1;
  }
  usedIds.add(candidate);
  return candidate;
}

function furnitureAsLayoutElement(
  contract: DeckMasterContractV1,
  role: keyof DeckMasterContractV1["furniture"],
  item: MasterFurniture,
  layoutId: string,
  zIndex: number,
  usedIds: Set<string>,
): SemanticMasterElement | null {
  const semanticRole = role === "logo"
    ? "brand-logo"
    : role === "pageNumber"
      ? "slide-number"
      : role;
  const type: SemanticMasterElement["type"] = item.kind === "logo"
    ? "image"
    : item.kind === "rect"
      ? "shape"
      : item.kind;
  const asset = item.kind === "logo" ? assetForFurniture(contract, item) : null;
  if (item.kind === "logo" && !asset) return null;
  if (item.kind === "text" && !item.text?.trim()) return null;
  if (item.kind === "line" && !item.stroke) return null;
  if (item.kind === "rect" && !item.fill && !item.stroke) return null;

  const style: SemanticMasterElementStyle = item.kind === "text" || item.kind === "pageNumber"
    ? {
        fontFamily: contract.fontManifest.primary,
        fontSize: item.kind === "pageNumber"
          ? contract.semanticStyles.caption.fontPt
          : contract.semanticStyles.note.fontPt,
        fontWeight: item.kind === "pageNumber"
          ? contract.semanticStyles.caption.fontWeight
          : contract.semanticStyles.note.fontWeight,
        color: item.fill ?? contract.tokens.colors.textSecondary,
      }
    : item.kind === "line"
      ? { stroke: item.stroke, strokeWidth: Math.max(1, item.bounds.height) }
      : item.kind === "rect"
        ? {
            shape: "rect",
            ...(item.fill ? { fill: item.fill } : {}),
            ...(item.stroke ? { stroke: item.stroke } : {}),
          }
        : {};
  const content: SemanticMasterElementContent = item.kind === "logo" && asset
    ? {
        role: semanticRole,
        assetId: asset.assetId,
        digest: asset.digest,
        mediaType: "image/png",
        altText: asset.altText,
        fit: "contain",
      }
    : {
        role: semanticRole,
        ...(item.kind === "text" || item.kind === "pageNumber"
          ? { text: item.text ?? "1" }
          : {}),
      };
  return {
    id: localFurnitureElementId(String(role), layoutId, usedIds),
    type,
    scope: { kind: "layout", layoutId },
    bounds: { ...item.bounds },
    rotation: 0,
    zIndex,
    visibility: "layoutOnly",
    editableByUser: true,
    editableByAI: false,
    locked: false,
    style,
    content,
  };
}

/**
 * Materializes legacy master-wide furniture once per layout, then hides the
 * shared source records. From this point on every edit targets a layout-local
 * semantic element and cannot leak into cover, section or another body layout.
 */
export function normalizeFurnitureForLayoutEditing(
  contract: DeckMasterContractV1,
): DeckMasterContractV1 {
  const furniture = Object.entries(contract.furniture) as Array<
    [keyof DeckMasterContractV1["furniture"], MasterFurniture]
  >;
  if (furniture.every(([, item]) => item.visibility === "hidden")) return contract;

  const layouts = [
    ...Object.entries(contract.layouts).map(([kind, layout]) => ({
      kind: kind as LayoutKind,
      id: layout.id,
    })),
    ...(contract.customLayouts ?? []).map((layout) => ({
      kind: "custom" as const,
      id: layout.id,
    })),
  ];
  const usedIds = allCanvasIds(contract);
  const localized = layouts.flatMap((layout) =>
    furniture.flatMap(([role, item], index) => {
      if (!furnitureVisibleOnLayout(item.visibility, layout.kind)) return [];
      const element = furnitureAsLayoutElement(
        contract,
        role,
        item,
        layout.id,
        FURNITURE_Z_BASE + index,
        usedIds,
      );
      return element ? [element] : [];
    }),
  );
  return {
    ...contract,
    furniture: Object.fromEntries(
      furniture.map(([role, item]) => [role, { ...item, visibility: "hidden" as const }]),
    ) as DeckMasterContractV1["furniture"],
    semanticElements: [...(contract.semanticElements ?? []), ...localized],
  };
}

export function normalizeMasterForLayoutEditing(
  contract: DeckMasterContractV1,
): DeckMasterContractV1 {
  return normalizeFurnitureForLayoutEditing(
    normalizeAiEditZonesForLayoutEditing(contract),
  );
}

export function normalizeMasterForPersistence(
  contract: DeckMasterContractV1,
): DeckMasterContractV1 {
  let changed = false;
  const aiEditZones = (contract.aiEditZones ?? []).map((zone) => {
    if (zone.emptyBehavior === "fallback" || zone.fallbackText === undefined) {
      return zone;
    }
    changed = true;
    const normalized = { ...zone };
    delete normalized.fallbackText;
    return normalized;
  });
  return changed ? { ...contract, aiEditZones } : contract;
}

function validateSemanticElement(
  contract: DeckMasterContractV1,
  element: SemanticMasterElement,
): SemanticMasterElement {
  if (!/^element:[a-z0-9][a-z0-9._-]*$/.test(element.id)) {
    throw new MasterCanvasAdapterError(`Invalid semantic element ID: ${element.id}`);
  }
  assertBounds(element.bounds);
  if (!Number.isFinite(element.rotation) || !Number.isInteger(element.zIndex)) {
    throw new MasterCanvasAdapterError("Element rotation and z-index must be finite");
  }
  if (element.scope.kind === "layout") findLayout(contract, element.scope.layoutId);
  if (element.content.crop) assertCrop(element.content.crop);
  return {
    ...element,
    scope: { ...element.scope },
    bounds: { ...element.bounds },
    style: { ...element.style, ...(element.style.lineDash ? { lineDash: [...element.style.lineDash] } : {}) },
    content: {
      ...element.content,
      ...(element.content.crop ? { crop: { ...element.content.crop } } : {}),
    },
  };
}

function sharedLogoGroup(element: SemanticMasterElement | null): string | null {
  return element?.type === "image" && element.content.role === "brand-logo" && element.content.sharedGroupId
    ? element.content.sharedGroupId
    : null;
}

function sharedLogoMembers(
  contract: DeckMasterContractV1,
  groupId: string,
): SemanticMasterElement[] {
  return (contract.semanticElements ?? []).filter(
    (element) => sharedLogoGroup(element) === groupId,
  );
}

function sharedLogoSyncTargets(
  contract: DeckMasterContractV1,
  elementId: string,
): SemanticMasterElement[] | null {
  const source = semanticElementEntry(contract, elementId);
  const groupId = sharedLogoGroup(source);
  if (!source || !groupId || source.content.sharedOverride) return null;
  return sharedLogoMembers(contract, groupId).filter(
    (element) => !element.content.sharedOverride,
  );
}

function sharedLogoBounds(
  safeArea: Bounds,
  anchor: SharedLogoAnchor,
): Bounds {
  const width = Math.min(180, safeArea.width);
  const height = Math.min(72, safeArea.height);
  const inset = Math.min(16, Math.max(0, safeArea.width - width), Math.max(0, safeArea.height - height));
  return {
    x: anchor.endsWith("Right")
      ? safeArea.x + safeArea.width - width - inset
      : safeArea.x + inset,
    y: anchor.startsWith("bottom")
      ? safeArea.y + safeArea.height - height - inset
      : safeArea.y + inset,
    width,
    height,
  };
}

function sharedLogoElementId(groupId: string, layoutId: string): string {
  const normalizedGroup = groupId.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+/, "") || "shared-logo";
  const normalizedLayout = layoutId.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+/, "") || "layout";
  return `element:${normalizedGroup}-${normalizedLayout}`;
}

function sharedLogoContentFrom(
  source: SemanticMasterElement,
  groupId: string,
): SemanticMasterElementContent {
  return {
    ...source.content,
    sharedGroupId: groupId,
    sharedOverride: false,
  };
}

function replaceLayout(
  contract: DeckMasterContractV1,
  layoutIdOrKind: string,
  replacement: MasterLayout | MasterCustomLayout,
): DeckMasterContractV1 {
  const { builtInKind } = findLayout(contract, layoutIdOrKind);
  if (builtInKind) {
    return {
      ...contract,
      layouts: { ...contract.layouts, [builtInKind]: replacement as MasterLayout },
    };
  }
  return {
    ...contract,
    customLayouts: (contract.customLayouts ?? []).map((layout) =>
      layout.id === layoutIdOrKind ? replacement as MasterCustomLayout : layout,
    ),
  };
}

/** Applies one undoable canvas transaction back to the semantic contract. */
export function applyMasterCanvasTransaction(
  contract: DeckMasterContractV1,
  layoutIdOrKind: string,
  transaction: MasterCanvasTransaction,
): DeckMasterContractV1 {
  if (transaction.type === "insertSharedLogo") {
    const layoutIds = [...new Set(transaction.layoutIds)].sort((left, right) => left.localeCompare(right, "en"));
    if (!layoutIds.length) throw new MasterCanvasAdapterError("A shared logo needs at least one layout");
    layoutIds.forEach((layoutId) => findLayout(contract, layoutId));
    const groupId = transaction.groupId ?? `shared-logo:${transaction.asset.assetId.slice("asset:".length)}`;
    if (!/^[a-z0-9][a-z0-9._:-]*$/i.test(groupId)) {
      throw new MasterCanvasAdapterError("Shared logo group ID is invalid");
    }
    if (sharedLogoMembers(contract, groupId).length) {
      throw new MasterCanvasAdapterError("Shared logo group already exists");
    }
    const occupied = allCanvasIds(contract);
    const bounds = sharedLogoBounds(contract.canvas.safeArea, transaction.anchor);
    const elements = layoutIds.map((layoutId, index) => {
      const id = sharedLogoElementId(groupId, layoutId);
      if (occupied.has(id)) throw new MasterCanvasAdapterError(`Duplicate shared logo element ID: ${id}`);
      occupied.add(id);
      return validateSemanticElement(contract, {
        id,
        type: "image",
        scope: { kind: "layout", layoutId },
        bounds: { ...bounds },
        rotation: 0,
        zIndex: 20_000 + index,
        visibility: "layoutOnly",
        editableByUser: true,
        editableByAI: false,
        locked: false,
        style: { opacity: 1 },
        content: {
          role: "brand-logo",
          assetId: transaction.asset.assetId,
          digest: transaction.asset.digest,
          altText: transaction.asset.altText,
          mediaType: "image/png",
          fit: "contain",
          sharedGroupId: groupId,
          sharedOverride: false,
        },
      });
    });
    const withoutCanonicalAsset = { ...contract.furniture.logo };
    delete withoutCanonicalAsset.assetId;
    return {
      ...contract,
      ...(contract.furniture.logo.assetId
        ? {
            brandAssets: { ...contract.brandAssets, logo: null },
            furniture: { ...contract.furniture, logo: withoutCanonicalAsset },
          }
        : {}),
      semanticElements: [...(contract.semanticElements ?? []), ...elements],
    };
  }

  if (transaction.type === "setSharedLogoOverride") {
    const source = semanticElementEntry(contract, transaction.elementId);
    if (!sharedLogoGroup(source)) throw new MasterCanvasAdapterError("Only shared logos can be overridden");
    return updateSemanticElement(contract, transaction.elementId, (element) => ({
      ...element,
      content: { ...element.content, sharedOverride: true },
    }));
  }

  if (transaction.type === "restoreSharedLogo") {
    const target = semanticElementEntry(contract, transaction.elementId);
    const groupId = sharedLogoGroup(target);
    if (!target || !groupId) throw new MasterCanvasAdapterError("Only shared logos can be restored");
    const source = sharedLogoMembers(contract, groupId)
      .filter((element) => !element.content.sharedOverride)
      .sort((left, right) => left.id.localeCompare(right.id, "en"))[0];
    if (!source) throw new MasterCanvasAdapterError("A shared logo needs a non-overridden source to restore");
    return updateSemanticElement(contract, transaction.elementId, (element) => ({
      ...element,
      bounds: { ...source.bounds },
      rotation: source.rotation,
      zIndex: source.zIndex,
      style: { ...source.style },
      content: sharedLogoContentFrom(source, groupId),
    }));
  }

  if (transaction.type === "addLayout") {
    if (!/^layout:[a-z0-9][a-z0-9._-]*$/.test(transaction.layout.id)) {
      throw new MasterCanvasAdapterError(`Invalid layout ID: ${transaction.layout.id}`);
    }
    if (
      Object.values(contract.layouts).some((layout) => layout.id === transaction.layout.id) ||
      (contract.customLayouts ?? []).some((layout) => layout.id === transaction.layout.id)
    ) {
      throw new MasterCanvasAdapterError(`Duplicate layout ID: ${transaction.layout.id}`);
    }
    const occupiedIds = allCanvasIds(contract);
    for (const slot of transaction.layout.slots) {
      if (occupiedIds.has(slot.id)) throw new MasterCanvasAdapterError(`Duplicate slot ID: ${slot.id}`);
      occupiedIds.add(slot.id);
      assertBounds(slot.bounds);
    }
    return {
      ...contract,
      customLayouts: [
        ...(contract.customLayouts ?? []),
        { ...transaction.layout, slots: transaction.layout.slots.map((slot) => ({ ...slot, bounds: { ...slot.bounds }, ownership: { ...slot.ownership } })) },
      ],
    };
  }

  if (transaction.type === "cloneLayout") {
    const source = findLayout(contract, transaction.layoutId).layout;
    if (!/^layout:[a-z0-9][a-z0-9._-]*$/.test(transaction.newLayoutId)) {
      throw new MasterCanvasAdapterError(`Invalid layout ID: ${transaction.newLayoutId}`);
    }
    const suffix = transaction.newLayoutId.slice("layout:".length);
    const cloned: MasterCustomLayout = {
      id: transaction.newLayoutId,
      name: transaction.name ?? `${source.name} copy`,
      kind: "custom",
      slots: source.slots.map((slot, index) => ({
        ...slot,
        id: `slot:${suffix}-${index + 1}`,
        bounds: { ...slot.bounds },
        ownership: { ...slot.ownership },
      })),
    };
    let next = applyMasterCanvasTransaction(contract, layoutIdOrKind, { type: "addLayout", layout: cloned });
    const ids = allCanvasIds(next);
    const copiedElements: SemanticMasterElement[] = [];
    for (const sourceElement of contract.semanticElements ?? []) {
      if (!isConfirmedFurnitureSemantic(sourceElement) || sourceElement.scope.kind !== "layout" || sourceElement.scope.layoutId !== source.id) continue;
      let copyNumber = 1;
      let id = defaultCopyId(sourceElement.id, copyNumber);
      while (ids.has(id)) id = defaultCopyId(sourceElement.id, ++copyNumber);
      ids.add(id);
      copiedElements.push(validateSemanticElement(next, {
        ...sourceElement,
        id,
        scope: { kind: "layout", layoutId: cloned.id },
      }));
    }
    return { ...next, semanticElements: [...(next.semanticElements ?? []), ...copiedElements] };
  }

  if (transaction.type === "deleteLayout") {
    if (!(contract.customLayouts ?? []).some((layout) => layout.id === transaction.layoutId)) {
      throw new MasterCanvasAdapterError("Required built-in layouts cannot be deleted");
    }
    return {
      ...contract,
      customLayouts: (contract.customLayouts ?? []).filter((layout) => layout.id !== transaction.layoutId),
      semanticElements: (contract.semanticElements ?? []).filter(
        (element) => element.scope.kind !== "layout" || element.scope.layoutId !== transaction.layoutId,
      ),
    };
  }

  if (transaction.type === "setBackground") {
    if (!/^#[0-9a-f]{6}$/i.test(transaction.color)) {
      throw new MasterCanvasAdapterError("Background must be a six-digit hex color");
    }
    return {
      ...contract,
      tokens: {
        ...contract.tokens,
        colors: { ...contract.tokens.colors, background: transaction.color },
      },
    };
  }

  if (transaction.type === "addAiEditZone") {
    if (allCanvasIds(contract).has(transaction.zone.id)) {
      throw new MasterCanvasAdapterError(`Duplicate canvas element ID: ${transaction.zone.id}`);
    }
    return {
      ...contract,
      aiEditZones: [
        ...(contract.aiEditZones ?? []),
        validateAiEditZone(contract, transaction.zone),
      ],
    };
  }

  if (transaction.type === "updateAiEditZoneGeometry") {
    const exists = (contract.aiEditZones ?? []).some((zone) => zone.id === transaction.zoneId);
    if (!exists) throw new MasterCanvasAdapterError(`Unknown AI edit zone: ${transaction.zoneId}`);
    const bounds = assertBounds(transaction.bounds);
    return {
      ...contract,
      aiEditZones: (contract.aiEditZones ?? []).map((zone) =>
        zone.id === transaction.zoneId ? { ...zone, bounds } : zone,
      ),
    };
  }

  if (transaction.type === "updateAiEditZone") {
    const zone = (contract.aiEditZones ?? []).find((candidate) => candidate.id === transaction.zoneId);
    if (!zone) throw new MasterCanvasAdapterError(`Unknown AI edit zone: ${transaction.zoneId}`);
    const next = { ...zone, ...transaction.patch } as AIEditZone & Record<string, unknown>;
    for (const [key, value] of Object.entries(transaction.patch)) {
      if (value === null || value === undefined) delete next[key];
    }
    return {
      ...contract,
      aiEditZones: (contract.aiEditZones ?? []).map((candidate) =>
        candidate.id === transaction.zoneId ? validateAiEditZone(contract, next) : candidate,
      ),
    };
  }

  if (transaction.type === "splitAiEditZoneByLayout") {
    const source = (contract.aiEditZones ?? []).find(
      (zone) => zone.id === transaction.zoneId,
    );
    if (!source) {
      throw new MasterCanvasAdapterError(
        `Unknown AI edit zone: ${transaction.zoneId}`,
      );
    }
    if (source.scope.kind !== "common") {
      throw new MasterCanvasAdapterError(
        "Only a shared AI edit zone can be split by layout",
      );
    }
    const layoutIds = [...new Set(transaction.layoutIds)];
    if (!layoutIds.length) {
      throw new MasterCanvasAdapterError(
        "AI edit zone split requires at least one layout",
      );
    }
    const usedIds = allCanvasIds(contract);
    usedIds.delete(source.id);
    const copies = layoutIds.map((layoutId) => {
      findLayout(contract, layoutId);
      const id = transaction.idFactory(layoutId);
      if (usedIds.has(id)) {
        throw new MasterCanvasAdapterError(
          `Duplicate canvas element ID: ${id}`,
        );
      }
      usedIds.add(id);
      return validateAiEditZone(contract, {
        ...source,
        id,
        scope: { kind: "layout", layoutId },
      });
    });
    return {
      ...contract,
      aiEditZones: (contract.aiEditZones ?? []).flatMap((zone) =>
        zone.id === source.id ? copies : [zone],
      ),
    };
  }

  if (transaction.type === "deleteAiEditZones") {
    const ids = new Set(transaction.zoneIds);
    return {
      ...contract,
      aiEditZones: (contract.aiEditZones ?? []).filter((zone) => !ids.has(zone.id)),
    };
  }

  if (transaction.type === "insertElement") {
    if (!isConfirmedFurnitureSemantic(transaction.element)) {
      throw new MasterCanvasAdapterError("Only confirmed master furniture can be inserted in furniture setup");
    }
    if (allCanvasIds(contract).has(transaction.element.id)) {
      throw new MasterCanvasAdapterError(`Duplicate canvas element ID: ${transaction.element.id}`);
    }
    return {
      ...contract,
      semanticElements: [
        ...(contract.semanticElements ?? []),
        validateSemanticElement(contract, transaction.element),
      ],
    };
  }

  if (transaction.type === "updateStyle") {
    const elementIds = [...new Set(transaction.elementIds.flatMap((id) =>
      sharedLogoSyncTargets(contract, id)?.map((element) => element.id) ?? [id],
    ))];
    return elementIds.reduce(
      (next, id) => updateSemanticElement(next, id, (element) => ({
        ...element,
        style: { ...element.style, ...transaction.style },
      })),
      contract,
    );
  }

  if (transaction.type === "updateContent") {
    const source = semanticElementEntry(contract, transaction.elementId);
    if (
      sharedLogoGroup(source) &&
      (transaction.content.assetId !== undefined || transaction.content.digest !== undefined || transaction.content.mediaType !== undefined || transaction.content.sharedGroupId !== undefined || transaction.content.sharedOverride !== undefined)
    ) {
      throw new MasterCanvasAdapterError("Shared logo asset and synchronization metadata use dedicated transactions");
    }
    const elementIds = sharedLogoSyncTargets(contract, transaction.elementId)?.map((element) => element.id) ?? [transaction.elementId];
    return elementIds.reduce(
      (next, id) => updateSemanticElement(next, id, (element) => ({
        ...element,
        content: {
          ...element.content,
          ...transaction.content,
          ...(transaction.content.crop ? { crop: assertCrop(transaction.content.crop) } : {}),
        },
      })),
      contract,
    );
  }

  if (transaction.type === "updateVisibility") {
    return [...new Set(transaction.elementIds)].reduce(
      (next, id) => updateSemanticElement(next, id, (element) => ({ ...element, visibility: transaction.visibility })),
      contract,
    );
  }

  if (transaction.type === "updateScope") {
    if (transaction.scope.kind === "layout") findLayout(contract, transaction.scope.layoutId);
    return [...new Set(transaction.elementIds)].reduce(
      (next, id) => updateSemanticElement(next, id, (element) => ({
        ...element,
        scope: { ...transaction.scope },
      })),
      contract,
    );
  }

  if (transaction.type === "updateOwnership") {
    return [...new Set(transaction.elementIds)].reduce(
      (next, id) => updateSemanticElement(next, id, (element) => ({
        ...element,
        editableByUser: transaction.editableByUser ?? element.editableByUser,
        editableByAI: transaction.editableByAI ?? element.editableByAI,
        locked: transaction.locked ?? element.locked,
      })),
      contract,
    );
  }

  if (transaction.type === "updateImagePresentation") {
    const semantic = semanticElementEntry(contract, transaction.elementId);
    if (!semantic) {
      if (transaction.asset !== undefined) {
        return applyMasterCanvasTransaction(contract, layoutIdOrKind, {
          type: "updateImage",
          elementId: transaction.elementId,
          asset: transaction.asset,
        });
      }
      throw new MasterCanvasAdapterError("Image presentation updates require a semantic image");
    }
    assertConfirmedFurnitureSemantic(contract, transaction.elementId);
    if (semantic.type !== "image") throw new MasterCanvasAdapterError("Element is not an image");
    if (
      sharedLogoGroup(semantic) &&
      (transaction.asset !== undefined || transaction.assetId !== undefined || transaction.digest !== undefined || transaction.mediaType !== undefined)
    ) {
      throw new MasterCanvasAdapterError("Shared logo instances keep one digest-pinned asset reference");
    }
    const elementIds = sharedLogoSyncTargets(contract, transaction.elementId)?.map((element) => element.id) ?? [transaction.elementId];
    const next = elementIds.reduce((current, id) => updateSemanticElement(current, id, (element) => {
      const content: SemanticMasterElementContent = {
        ...element.content,
        ...(transaction.asset ? {
          assetId: transaction.asset.assetId,
          digest: transaction.asset.digest,
          altText: transaction.asset.altText,
        } : {}),
        ...(transaction.assetId === undefined ? {} : { assetId: transaction.assetId }),
        ...(transaction.digest === undefined ? {} : { digest: transaction.digest }),
        ...(transaction.mediaType === undefined ? {} : { mediaType: transaction.mediaType }),
        ...(transaction.altText === undefined ? {} : { altText: transaction.altText }),
        ...(transaction.fit === undefined ? {} : { fit: transaction.fit }),
      };
      if (transaction.crop === null) delete content.crop;
      else if (transaction.crop) content.crop = assertCrop(transaction.crop);
      return { ...element, content };
    }), contract);
    return next;
  }

  if (transaction.type === "updateGeometryBatch") {
    const updates = [...transaction.updates.reduce((expanded, update) => {
      const targets = sharedLogoSyncTargets(contract, update.elementId) ?? [semanticElementEntry(contract, update.elementId)].filter((item): item is SemanticMasterElement => item !== null);
      if (!targets.length) {
        expanded.set(update.elementId, update);
      } else {
        targets.forEach((target) => expanded.set(target.id, { ...update, elementId: target.id }));
      }
      return expanded;
    }, new Map<string, (typeof transaction.updates)[number]>()).values()];
    for (const update of updates) {
      assertElementExists(contract, layoutIdOrKind, update.elementId);
      assertBounds(update.bounds);
      if (!Number.isFinite(update.rotation ?? 0)) throw new MasterCanvasAdapterError("Rotation must be finite");
      if (!semanticElementEntry(contract, update.elementId) && (update.rotation ?? 0) !== 0) {
        throw new MasterCanvasAdapterError("Legacy furniture and slots cannot persist rotation");
      }
    }
    return updates.reduce(
      (next, update) => {
        const moved = updateBounds(next, layoutIdOrKind, update.elementId, update.bounds);
        return semanticElementEntry(moved, update.elementId) && update.rotation !== undefined
          ? updateSemanticElement(moved, update.elementId, (element) => ({ ...element, rotation: update.rotation! }))
          : moved;
      },
      contract,
    );
  }

  if (transaction.type === "updateGeometry") {
    const semantic = semanticElementEntry(contract, transaction.elementId);
    const syncTargets = sharedLogoSyncTargets(contract, transaction.elementId);
    if (syncTargets) {
      return applyMasterCanvasTransaction(contract, layoutIdOrKind, {
        type: "updateGeometryBatch",
        updates: syncTargets.map((target) => ({
          elementId: target.id,
          bounds: transaction.bounds,
          ...(transaction.rotation === undefined ? {} : { rotation: transaction.rotation }),
        })),
      });
    }
    if (!semantic && (transaction.rotation ?? 0) !== 0) {
      throw new MasterCanvasAdapterError("Legacy furniture and slots cannot persist rotation");
    }
    const moved = updateBounds(contract, layoutIdOrKind, transaction.elementId, transaction.bounds);
    return semantic && transaction.rotation !== undefined
      ? updateSemanticElement(moved, transaction.elementId, (element) => ({ ...element, rotation: transaction.rotation! }))
      : moved;
  }

  if (transaction.type === "updateText") {
    const semantic = semanticElementEntry(contract, transaction.elementId);
    if (semantic) {
      if (semantic.content.text === transaction.text) return contract;
      return updateSemanticElement(contract, transaction.elementId, (element) => ({
        ...element,
        content: { ...element.content, text: transaction.text },
      }));
    }
    const entry = furnitureEntry(contract, transaction.elementId);
    if (!entry || (entry[1].kind !== "text" && entry[1].kind !== "pageNumber")) {
      throw new MasterCanvasAdapterError("Only text furniture can persist authored text");
    }
    if (entry[1].text === transaction.text) return contract;
    return updateElement(
      contract,
      layoutIdOrKind,
      transaction.elementId,
      (item) => ({ ...item, text: transaction.text }),
      (item) => item,
    );
  }

  if (transaction.type === "updateImage") {
    if (semanticElementEntry(contract, transaction.elementId)) {
      assertConfirmedFurnitureSemantic(contract, transaction.elementId);
      return applyMasterCanvasTransaction(contract, layoutIdOrKind, {
        type: "updateImagePresentation",
        elementId: transaction.elementId,
        asset: transaction.asset,
      });
    }
    const entry = furnitureEntry(contract, transaction.elementId);
    if (!entry || entry[0] !== "logo" || entry[1].kind !== "logo") {
      throw new MasterCanvasAdapterError("Only the canonical logo binding can persist an image");
    }
    const logo = transaction.asset;
    const withoutAsset = { ...entry[1] };
    delete withoutAsset.assetId;
    const nextLogo: MasterFurniture = logo
      ? { ...withoutAsset, assetId: logo.assetId }
      : withoutAsset;
    return {
      ...contract,
      brandAssets: { ...contract.brandAssets, logo },
      furniture: { ...contract.furniture, logo: nextLogo },
    };
  }

  if (transaction.type === "paste") {
    if (!transaction.items.length) return contract;
    const { layout } = findLayout(contract, layoutIdOrKind);
    const ids = allCanvasIds(contract);
    let nextPlaceholderIndex = Math.max(
      -1,
      ...layout.slots.map((slot) => slot.placeholderIndex),
    ) + 1;
    const slots: MasterSlot[] = [];
    const semanticElements: SemanticMasterElement[] = [];

    for (const item of transaction.items) {
      const sourceId = item.kind === "semantic" ? item.element.id : item.slot.id;
      let copyNumber = 1;
      let id = transaction.idFactory?.(sourceId, copyNumber) ?? defaultCopyId(sourceId, copyNumber);
      while (ids.has(id)) {
        copyNumber += 1;
        id = transaction.idFactory?.(sourceId, copyNumber) ?? defaultCopyId(sourceId, copyNumber);
      }
      ids.add(id);

      if (item.kind === "semantic") {
        if (
          !isConfirmedFurnitureSemantic(item.element) ||
          item.element.locked ||
          item.element.content.sharedGroupId
        ) {
          throw new MasterCanvasAdapterError("Clipboard contains a non-duplicable semantic element");
        }
        if (!/^element:[a-z0-9][a-z0-9._-]*$/.test(id)) {
          throw new MasterCanvasAdapterError(`Invalid pasted element ID: ${id}`);
        }
        semanticElements.push(validateSemanticElement(contract, {
          ...item.element,
          id,
          scope: { kind: "layout", layoutId: layout.id },
          visibility: "layoutOnly",
          bounds: {
            ...item.element.bounds,
            x: item.element.bounds.x + 16,
            y: item.element.bounds.y + 16,
          },
        }));
        continue;
      }

      if (!/^slot:[a-z0-9][a-z0-9._-]*$/.test(id)) {
        throw new MasterCanvasAdapterError(`Invalid pasted slot ID: ${id}`);
      }
      slots.push({
        ...item.slot,
        id,
        placeholderIndex: nextPlaceholderIndex,
        bounds: {
          ...item.slot.bounds,
          x: item.slot.bounds.x + 16,
          y: item.slot.bounds.y + 16,
        },
        ownership: { ...item.slot.ownership },
      });
      nextPlaceholderIndex += 1;
    }

    return replaceLayout(
      {
        ...contract,
        semanticElements: [...(contract.semanticElements ?? []), ...semanticElements],
      },
      layoutIdOrKind,
      { ...layout, slots: [...layout.slots, ...slots] },
    );
  }

  const scene = masterContractToCanvasScene(contract, layoutIdOrKind);
  const requestedIds = [...new Set(transaction.elementIds)];
  const uniqueIds = [...new Set(
    transaction.type === "delete"
      ? requestedIds.flatMap((id) => {
          const groupId = sharedLogoGroup(semanticElementEntry(contract, id));
          return groupId ? sharedLogoMembers(contract, groupId).map((element) => element.id) : [id];
        })
      : requestedIds,
  )];
  const elements = requestedIds.map((id) => {
    assertElementExists(contract, layoutIdOrKind, id);
    const element = scene.elements.find((item) => item.id === id);
    if (!element) throw new MasterCanvasAdapterError(`Only confirmed furniture can be edited: ${id}`);
    return element;
  });

  if (transaction.type === "align") {
    if (elements.length < 1) return contract;
    const selection = selectedEnvelope(elements);
    const target = transaction.relativeTo === "safeArea" ? contract.canvas.safeArea : selection;
    let next = elements.reduce(
      (next, item) =>
        updateBounds(
          next,
          layoutIdOrKind,
          item.id,
          alignBounds(item.bounds, target, selection, transaction.alignment, transaction.relativeTo),
        ),
      contract,
    );
    for (const item of elements) {
      const syncTargets = sharedLogoSyncTargets(contract, item.id);
      const moved = semanticElementEntry(next, item.id);
      if (!syncTargets || !moved) continue;
      next = syncTargets.reduce(
        (current, shared) => updateBounds(current, layoutIdOrKind, shared.id, moved.bounds),
        next,
      );
    }
    return next;
  }

  const furniture = elements.filter((item) => item.layer === "furniture");
  if (furniture.length > 0 && transaction.type === "delete") {
    let next = contract;
    for (const item of furniture) {
      const entry = furnitureEntry(next, item.id);
      if (!entry) throw new MasterCanvasAdapterError(`Unknown fixed furniture: ${item.id}`);
      if (entry[0] === "logo" && entry[1].kind === "logo") {
        next = applyMasterCanvasTransaction(next, layoutIdOrKind, {
          type: "updateImage",
          elementId: entry[1].id,
          asset: null,
        });
        continue;
      }
      if (entry[1].kind === "text") {
        next = updateElement(
          next,
          layoutIdOrKind,
          entry[1].id,
          (candidate) => ({ ...candidate, text: "" }),
          (candidate) => candidate,
        );
        continue;
      }
      throw new MasterCanvasAdapterError("Only bound logos and fixed text can be deleted");
    }
    const furnitureIds = new Set(furniture.map((item) => item.id));
    const remainingIds = uniqueIds.filter((id) => !furnitureIds.has(id));
    return remainingIds.length
      ? applyMasterCanvasTransaction(next, layoutIdOrKind, {
          type: "delete",
          elementIds: remainingIds,
        })
      : next;
  }

  if (furniture.length > 0) {
    throw new MasterCanvasAdapterError(`${transaction.type} is not representable for fixed master furniture`);
  }

  const { layout } = findLayout(contract, layoutIdOrKind);
  if (transaction.type === "delete") {
    for (const id of uniqueIds) {
      const semantic = semanticElementEntry(contract, id);
      if (semantic && semantic.scope.kind === "common" && !sharedLogoGroup(semantic)) {
        throw new MasterCanvasAdapterError("Common semantic elements require an explicit shared deletion rule");
      }
      const slot = layout.slots.find((candidate) => candidate.id === id);
      if (slot && slot.role !== "content") {
        throw new MasterCanvasAdapterError("Required title/body placeholders cannot be deleted");
      }
    }
    const ids = new Set(uniqueIds);
    const withoutElements = {
      ...contract,
      semanticElements: (contract.semanticElements ?? []).filter((element) => !ids.has(element.id)),
    };
    return replaceLayout(withoutElements, layoutIdOrKind, {
      ...layout,
      slots: layout.slots.filter((slot) => !ids.has(slot.id)),
    });
  }

  if (transaction.type === "reorder") {
    const ids = new Set(uniqueIds);
    const semantic = contract.semanticElements ?? [];
    const zValues = semantic.map((item) => item.zIndex);
    const floor = Math.min(0, ...zValues);
    const ceiling = Math.max(0, ...zValues);
    const reordered = semantic.map((item) => {
      if (!ids.has(item.id)) return item;
      const zIndex = transaction.direction === "front" ? ceiling + 1
        : transaction.direction === "back" ? floor - 1
          : item.zIndex + (transaction.direction === "forward" ? 1 : -1);
      return { ...item, zIndex };
    });
    return replaceLayout(
      { ...contract, semanticElements: reordered },
      layoutIdOrKind,
      { ...layout, slots: moveSelection(layout.slots, ids, transaction.direction) },
    );
  }

  const ids = allCanvasIds(contract);
  let nextPlaceholderIndex = Math.max(-1, ...layout.slots.map((slot) => slot.placeholderIndex)) + 1;
  const copies: MasterSlot[] = [];
  const semanticCopies: SemanticMasterElement[] = [];
  for (const sourceId of uniqueIds) {
    let copyNumber = 1;
    let id = transaction.idFactory?.(sourceId, copyNumber) ?? defaultCopyId(sourceId, copyNumber);
    while (ids.has(id)) {
      copyNumber += 1;
      id = transaction.idFactory?.(sourceId, copyNumber) ?? defaultCopyId(sourceId, copyNumber);
    }
    ids.add(id);
    const semanticSource = semanticElementEntry(contract, sourceId);
    if (semanticSource) {
      if (!/^element:[a-z0-9][a-z0-9._-]*$/.test(id)) {
        throw new MasterCanvasAdapterError(`Invalid duplicated element ID: ${id}`);
      }
      semanticCopies.push(validateSemanticElement(contract, {
        ...semanticSource,
        id,
        bounds: { ...semanticSource.bounds, x: semanticSource.bounds.x + 16, y: semanticSource.bounds.y + 16 },
      }));
      continue;
    }
    const source = layout.slots.find((slot) => slot.id === sourceId)!;
    if (!/^slot:[a-z0-9][a-z0-9._-]*$/.test(id)) {
      throw new MasterCanvasAdapterError(`Invalid duplicated slot ID: ${id}`);
    }
    copies.push({
      ...source,
      id,
      placeholderIndex: nextPlaceholderIndex,
      bounds: { ...source.bounds, x: source.bounds.x + 16, y: source.bounds.y + 16 },
      ownership: { ...source.ownership },
    });
    nextPlaceholderIndex += 1;
  }
  return replaceLayout(
    { ...contract, semanticElements: [...(contract.semanticElements ?? []), ...semanticCopies] },
    layoutIdOrKind,
    { ...layout, slots: [...layout.slots, ...copies] },
  );
}
