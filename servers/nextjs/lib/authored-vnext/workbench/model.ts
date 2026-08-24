export type DeckId = `deck:${string}`;
export type MasterId = `master:${string}`;
export type SlideId = `slide:${string}`;
/** Stable authored QID. Canonical Scenes retain the HTML `data-qid` token. */
export type ElementId = string;
export type AssetId = `asset:${string}`;
export type OperationId = `op:${string}`;
export type Digest = `sha256:${string}`;
export type SceneDataPayload = Readonly<Record<string, unknown>>;
/** Native authored-vNext chart types supported by the Scene and OOXML adapters. */
export type NativeChartType = "bar" | "column" | "line" | "area" | "pie" | "donut";

export interface Bounds {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/**
 * Server-sealed per-element capabilities.  A false capability is an explicit
 * product boundary, never an invitation to synthesize HTML or raster edits.
 */
export interface ElementEditability {
  readonly text: boolean;
  readonly geometry: boolean;
  readonly asset: boolean;
  readonly data: boolean;
  readonly style: boolean;
}

export interface CasPin {
  readonly deckRevision: number;
  readonly deckDigest: Digest;
  readonly sceneDigest: Digest;
  readonly slideRevision: number;
  readonly slideDigest: Digest;
  readonly masterDigest: Digest;
}

interface BodyNodeBase {
  readonly id: ElementId;
  readonly layer: "body";
  readonly zOrder: number;
  readonly bounds: Bounds;
  readonly rotation: number;
  readonly semanticRole: string;
  readonly editability: ElementEditability;
}

export interface TextBodyNode extends BodyNodeBase {
  readonly kind: "text";
  /** Canonical Scene roles are extensible (`card-body`, `metric-label`, …). */
  readonly role: string;
  readonly text: string;
  readonly style?: {
    readonly color?: string;
    readonly fontFamily?: string;
    readonly fontSize?: number;
    readonly fontWeight?: number;
    readonly textAlign?: "left" | "center" | "right" | "justify";
  };
}

export interface ImageBodyNode extends BodyNodeBase {
  /** `asset` is retained only for frozen pre-Scene compatibility snapshots. */
  readonly kind: "image" | "asset";
  readonly assetId: AssetId;
  readonly digest: Digest;
  readonly altText: string;
  /** `crop` is the canonical Scene IR field; `fit` remains a compatibility alias. */
  readonly crop: "contain" | "cover";
  readonly fit: "contain" | "cover";
  readonly mediaType?: "image/png" | "image/jpeg" | "image/svg+xml" | "image/webp";
  readonly storageReference?: string;
  readonly sizeBytes?: number;
  readonly cropGeometry?: { readonly left: number; readonly top: number; readonly right: number; readonly bottom: number };
}

/** @deprecated Use ImageBodyNode. */
export type AssetBodyNode = ImageBodyNode;

export interface ShapeBodyNode extends BodyNodeBase {
  readonly kind: "shape";
  readonly shape: string;
  /** Canonical Scene paint is retained for preview/export fidelity. */
  readonly fill?: string;
  readonly stroke?: string;
  readonly strokeWidth?: number;
}

export interface ConnectorBodyNode extends BodyNodeBase {
  readonly kind: "connector";
  readonly fromElementId: ElementId;
  readonly toElementId: ElementId;
}

export interface TableBodyNode extends BodyNodeBase {
  readonly kind: "table";
  readonly dataId: `data:${string}`;
  readonly dataDigest: Digest;
  readonly rows: number;
  readonly columns: number;
  readonly dataPayload?: SceneDataPayload;
}

export interface ChartBodyNode extends BodyNodeBase {
  readonly kind: "chart";
  readonly dataId: `data:${string}`;
  readonly dataDigest: Digest;
  readonly chartType: NativeChartType;
  readonly accessibilitySummary?: string;
  readonly dataPayload?: SceneDataPayload;
}

/**
 * The semantic compiler seals this decision for every authored QID.  The
 * Workbench only displays it; it must not infer native ownership from local
 * preview support or editability flags.
 */
export interface HybridOwnerDecision {
  readonly ownerId: ElementId;
  readonly decision: "nativeEditable" | "rasterIsland" | "publicationBlocked";
  readonly capabilityId: string;
  readonly reasonCode?: string;
}

export interface HybridRasterIsland {
  readonly islandId: `island:${string}`;
  readonly sourceOwnerIds: readonly ElementId[];
  readonly assetId: AssetId;
  readonly assetDigest: Digest;
  readonly zBand: { readonly minZ: number; readonly maxZ: number };
  readonly rasterPlaneDigest: Digest;
}

export interface SlideHybridExportPlan {
  readonly schemaVersion: "slide-hybrid-export-plan/v1";
  readonly ownerDecisions: readonly HybridOwnerDecision[];
  readonly rasterIslands: readonly HybridRasterIsland[];
}

export interface GroupBodyNode extends BodyNodeBase {
  readonly kind: "group";
  readonly childElementIds: readonly ElementId[];
}

export type BodyNode =
  | TextBodyNode
  | ImageBodyNode
  | ShapeBodyNode
  | ConnectorBodyNode
  | TableBodyNode
  | ChartBodyNode
  | GroupBodyNode;

export interface SlideDocument {
  readonly id: SlideId;
  readonly revision: number;
  readonly digest: Digest;
  readonly layoutId: string;
  readonly nodes: readonly BodyNode[];
  /** Present only when the server sealed the bounded Hybrid ownership plan. */
  readonly hybridExport?: SlideHybridExportPlan;
}

export interface DeckDocument {
  readonly schemaVersion: "authored-workbench/v1";
  readonly id: DeckId;
  readonly revision: number;
  readonly digest: Digest;
  readonly sceneDigest: Digest;
  /** Sealed Scene manifests retained so preview/mutation never guess bytes or data. */
  readonly assetManifest: readonly SceneAssetReference[];
  readonly dataManifest: readonly SceneDataReference[];
  readonly master: {
    readonly id: MasterId;
    readonly revision: number;
    readonly digest: Digest;
    readonly layer: "master";
    readonly locked: true;
  };
  readonly slides: readonly SlideDocument[];
}

export interface SceneAssetReference {
  readonly assetId: AssetId;
  readonly digest: Digest;
  readonly mediaType: "image/png" | "image/jpeg" | "image/svg+xml" | "image/webp";
  readonly storageReference: string;
  readonly sizeBytes: number;
}

export interface SceneDataReference {
  readonly dataId: `data:${string}`;
  readonly digest: Digest;
  readonly kind: "table" | "chart";
  readonly payload: SceneDataPayload;
}

interface OperationBase {
  readonly operationId: OperationId;
  readonly slideId: SlideId;
  readonly base: CasPin;
}

export interface TextReplaceOperation extends OperationBase {
  readonly kind: "text.replace";
  readonly elementId: ElementId;
  readonly text: string;
}

export interface GeometryUpdateOperation extends OperationBase {
  readonly kind: "geometry.update";
  readonly elementId: ElementId;
  readonly bounds: Bounds;
}

export interface AssetSetOperation extends OperationBase {
  readonly kind: "asset.set";
  readonly elementId: ElementId;
  readonly assetId: AssetId;
  readonly digest: Digest;
  readonly altText: string;
  readonly crop: "contain" | "cover";
  readonly mediaType?: SceneAssetReference["mediaType"];
  readonly storageReference?: string;
  readonly sizeBytes: number;
}

export interface DataSetOperation extends OperationBase {
  readonly kind: "data.set";
  readonly elementId: ElementId;
  readonly dataDigest: Digest;
  readonly dataId: `data:${string}`;
  readonly dataPayload: SceneDataPayload;
  readonly rows?: number;
  readonly columns?: number;
  readonly chartType?: NativeChartType;
  readonly accessibilitySummary?: string;
}

export interface LayoutApplyOperation extends OperationBase {
  readonly kind: "layout.apply";
  readonly layoutId: string;
}

export type WorkbenchOperation =
  | TextReplaceOperation
  | GeometryUpdateOperation
  | AssetSetOperation
  | DataSetOperation
  | LayoutApplyOperation;

export type DocumentPatch =
  | {
      readonly kind: "text.replace";
      readonly slideId: SlideId;
      readonly elementId: ElementId;
      readonly before: string;
      readonly after: string;
    }
  | {
      readonly kind: "geometry.update";
      readonly slideId: SlideId;
      readonly elementId: ElementId;
      readonly before: Bounds;
      readonly after: Bounds;
    }
  | {
      readonly kind: "asset.set";
      readonly slideId: SlideId;
      readonly elementId: ElementId;
      readonly before: Pick<ImageBodyNode, "assetId" | "digest" | "altText" | "crop" | "fit" | "mediaType" | "storageReference" | "sizeBytes">;
      readonly after: Pick<ImageBodyNode, "assetId" | "digest" | "altText" | "crop" | "fit" | "mediaType" | "storageReference" | "sizeBytes">;
    }
  | {
      readonly kind: "data.set";
      readonly slideId: SlideId;
      readonly elementId: ElementId;
      readonly before: Pick<TableBodyNode | ChartBodyNode, "dataId" | "dataDigest" | "dataPayload">;
      readonly after: Pick<TableBodyNode | ChartBodyNode, "dataId" | "dataDigest" | "dataPayload"> & Partial<Pick<TableBodyNode, "rows" | "columns">> & Partial<Pick<ChartBodyNode, "chartType" | "accessibilitySummary">>;
    }
  | {
      readonly kind: "layout.apply";
      readonly slideId: SlideId;
      readonly before: string;
      readonly after: string;
    };

export interface HistoryEntry {
  readonly operationId: OperationId;
  readonly forward: readonly DocumentPatch[];
  readonly inverse: readonly DocumentPatch[];
}

/** A server-rejected command remains auditable but is never replayed automatically. */
export type OutboxStatus = "queued" | "sending" | "conflict" | "failed";

export interface OutboxEntry {
  readonly operation: WorkbenchOperation;
  readonly patch: DocumentPatch;
  readonly status: OutboxStatus;
  readonly errorCode?: string;
  readonly source: "user" | "undo" | "redo";
  readonly createdAt: string;
}

export interface WorkbenchError {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
}

export type ResourceState<T> =
  | { readonly status: "empty" }
  | { readonly status: "loading"; readonly lastKnownGood?: T }
  | { readonly status: "ready"; readonly data: T }
  | { readonly status: "stale"; readonly data: T; readonly reason: string }
  | { readonly status: "error"; readonly error: WorkbenchError; readonly lastKnownGood?: T };

export interface CasConflict {
  readonly operationId: OperationId;
  readonly code: string;
  readonly expected: CasPin;
  readonly actual?: {
    readonly deckRevision: number;
    readonly deckDigest: Digest;
  };
  readonly rebase: "required" | "rebasing" | "failed";
  readonly message?: string;
}

export interface RegenerateRequest {
  readonly requestId: string;
  readonly scope: "bodyOnly";
  readonly slideId: SlideId;
  readonly layoutId: string;
  readonly body: string;
  readonly pin: CasPin;
}

export interface RegenerationCandidate {
  readonly request: RegenerateRequest;
  readonly candidateDigest: Digest;
  readonly candidateSlide: SlideDocument;
  readonly serverDeck: DeckDocument;
  readonly baseline: DeckDocument;
}

export type RegenerationState =
  | { readonly status: "idle" }
  | { readonly status: "loading"; readonly request: RegenerateRequest }
  | { readonly status: "preview"; readonly candidate: RegenerationCandidate }
  | { readonly status: "conflict"; readonly request: RegenerateRequest; readonly error: WorkbenchError }
  | { readonly status: "error"; readonly request: RegenerateRequest; readonly error: WorkbenchError };

export interface WorkbenchState {
  readonly deck: ResourceState<DeckDocument>;
  readonly activeSlideId: SlideId | null;
  readonly outbox: readonly OutboxEntry[];
  readonly undoStack: readonly HistoryEntry[];
  readonly redoStack: readonly HistoryEntry[];
  readonly conflict: CasConflict | null;
  readonly regeneration: RegenerationState;
}

export interface OperationDispatch {
  readonly effect: "dispatchOperation";
  readonly operation: WorkbenchOperation;
}

export interface BodyRegenerateDispatch {
  readonly effect: "regenerateBody";
  readonly request: RegenerateRequest;
}

export type WorkbenchEffect = OperationDispatch | BodyRegenerateDispatch;

export function createEmptyWorkbenchState(): WorkbenchState {
  return {
    deck: { status: "empty" },
    activeSlideId: null,
    outbox: [],
    undoStack: [],
    redoStack: [],
    conflict: null,
    regeneration: { status: "idle" },
  };
}

export function lastKnownGood<T>(resource: ResourceState<T>): T | undefined {
  switch (resource.status) {
    case "ready":
    case "stale":
      return resource.data;
    case "loading":
    case "error":
      return resource.lastKnownGood;
    case "empty":
      return undefined;
  }
}
