export type DeckId = `deck:${string}`;
export type MasterId = `master:${string}`;
export type SlideId = `slide:${string}`;
export type ElementId = `element:${string}`;
export type AssetId = `asset:${string}`;
export type OperationId = `op:${string}`;
export type Digest = `sha256:${string}`;

export interface Bounds {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface CasPin {
  readonly deckRevision: number;
  readonly deckDigest: Digest;
  readonly slideRevision: number;
  readonly masterDigest: Digest;
}

interface BodyNodeBase {
  readonly id: ElementId;
  readonly layer: "body";
  readonly editable: true;
  readonly bounds: Bounds;
}

export interface TextBodyNode extends BodyNodeBase {
  readonly kind: "text";
  readonly role: "title" | "body" | "caption" | "source";
  readonly text: string;
}

export interface AssetBodyNode extends BodyNodeBase {
  readonly kind: "asset";
  readonly assetId: AssetId;
  readonly digest: Digest;
  readonly altText: string;
}

export type BodyNode = TextBodyNode | AssetBodyNode;

export interface SlideDocument {
  readonly id: SlideId;
  readonly revision: number;
  readonly layoutId: string;
  readonly nodes: readonly BodyNode[];
}

export interface DeckDocument {
  readonly schemaVersion: "authored-workbench/v1";
  readonly id: DeckId;
  readonly revision: number;
  readonly digest: Digest;
  readonly master: {
    readonly id: MasterId;
    readonly revision: number;
    readonly digest: Digest;
    readonly layer: "master";
    readonly locked: true;
  };
  readonly slides: readonly SlideDocument[];
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
}

export interface LayoutApplyOperation extends OperationBase {
  readonly kind: "layout.apply";
  readonly layoutId: string;
}

export type WorkbenchOperation =
  | TextReplaceOperation
  | GeometryUpdateOperation
  | AssetSetOperation
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
      readonly before: Pick<AssetBodyNode, "assetId" | "digest" | "altText">;
      readonly after: Pick<AssetBodyNode, "assetId" | "digest" | "altText">;
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

export type OutboxStatus = "queued" | "sending" | "conflict";

export interface OutboxEntry {
  readonly operation: WorkbenchOperation;
  readonly patch: DocumentPatch;
  readonly status: OutboxStatus;
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
