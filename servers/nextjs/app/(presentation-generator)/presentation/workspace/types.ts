export const KNOWN_ASSET_STATUSES = [
  "uploaded",
  "scanning",
  "processing",
  "ready",
  "failed",
  "quarantined",
  "expired",
  "deleted",
] as const;

export type KnownAssetStatus = (typeof KNOWN_ASSET_STATUSES)[number];

export const KNOWN_OPERATION_STATUSES = [
  "queued",
  "waiting_for_assets",
  "blocked",
  "running",
  "approval_required",
  "awaiting_approval",
  "cancelling",
  "succeeded",
  "failed",
  "cancelled",
  "dead_letter",
] as const;

export type KnownOperationStatus = (typeof KNOWN_OPERATION_STATUSES)[number];

export const KNOWN_OPERATION_TYPES = [
  "presentation.generate",
  "presentation.edit",
  "presentation.delete",
  "slide.edit",
  "slide.regenerate",
  "slide.delete",
  "version.restore",
  "template.apply",
  "quality.review",
  "export.pptx",
  "export.pdf",
] as const;

export type KnownOperationType = (typeof KNOWN_OPERATION_TYPES)[number];

export function isKnownWorkspaceOperationType(
  value: unknown,
): value is KnownOperationType {
  return (
    typeof value === "string" &&
    (KNOWN_OPERATION_TYPES as readonly string[]).includes(value)
  );
}

export interface WorkspaceAttachment {
  id: string;
  name: string;
  status: string;
  stateAtTime: string;
  currentState: string;
  mimeType?: string;
  size?: number;
  sha256?: string;
  error?: string;
  failureCode?: string;
  downloadUrl?: string;
  previewUrl?: string;
  downloadAction?: string;
  previewAction?: string;
}

export interface WorkspaceMessageAnchor {
  anchorType: "message";
  id: string;
  position: number;
  role: "user" | "assistant" | "system" | "unknown";
  content: string;
  createdAt?: string;
  attachments: WorkspaceAttachment[];
  generationSettings?: Record<string, unknown>;
}

export interface WorkspaceOperationEvent {
  id: string;
  operationId: string;
  operationVersion?: number;
  sequence: number;
  cursor?: string;
  /** Durable top-level anchor position used to acknowledge off-screen events. */
  operationAnchorPosition?: number;
  type: string;
  status?: string;
  message?: string;
  progress?: number;
  error?: string;
  retryable?: boolean;
  result?: Record<string, unknown>;
  createdAt?: string;
}

export type WorkspaceOperationActionId =
  "approve" | "reject" | "cancel" | "retry" | "undo" | "download" | "focus";

export interface WorkspaceAllowedAction {
  id: WorkspaceOperationActionId;
  label: string;
  variant: "primary" | "secondary" | "danger";
  enabled: boolean;
  reason?: string;
}

export interface WorkspaceOperationActor {
  type?: string;
  id?: string;
  label?: string;
}

export interface WorkspaceOperationAnchor {
  anchorType: "operation";
  id: string;
  operationId: string;
  position: number;
  operationType: string;
  title: string;
  status: string;
  retryOfOperationId?: string;
  approvalExpiresAt?: string;
  version?: number;
  progress?: number;
  error?: string;
  retryable?: boolean;
  result?: Record<string, unknown>;
  actor?: WorkspaceOperationActor;
  target?: Record<string, unknown>;
  expectedRevision?: number;
  resultRevision?: number;
  activitySummary?: string;
  createdAt?: string;
  updatedAt?: string;
  events: WorkspaceOperationEvent[];
  allowedActions: WorkspaceAllowedAction[];
}

export interface WorkspaceUnknownAnchor {
  anchorType: "unknown";
  id: string;
  position: number;
  rawType: string;
}

export type WorkspaceTimelineAnchor =
  WorkspaceMessageAnchor | WorkspaceOperationAnchor | WorkspaceUnknownAnchor;

export interface WorkspaceTimelinePage {
  items: WorkspaceTimelineAnchor[];
  nextBeforePosition?: number;
  eventHighWatermark?: string;
  hasMore: boolean;
  revision?: number;
  /** Only an explicitly supported envelope may authorize mutations. */
  writesSafe: boolean;
}

export interface WorkspaceActionReceipt {
  contract: "v1" | "legacy";
  schemaVersion?: number;
  receiptId?: string;
  operationId?: string;
  status?: string;
  duplicate?: boolean;
  timeline: WorkspaceTimelinePage;
}

export interface WorkspaceEventPage {
  events: WorkspaceOperationEvent[];
  eventHighWatermark?: string;
  cursorSafe?: boolean;
}

export interface WorkspaceProjection {
  items: WorkspaceTimelineAnchor[];
  messageBeforePosition?: number;
  eventHighWatermark?: string;
  revision?: number;
  hasMore: boolean;
  seenEventKeys: Record<string, true>;
  rebaseRequired: boolean;
  orphanOperationIds: string[];
  /** Undefined until the first timeline page; false is monotonic for the session. */
  writesSafe?: boolean;
}

export interface WorkspaceCapability {
  supported: boolean;
  timeline: boolean;
  commands: boolean;
  events: boolean;
  reason?: string;
}

export type WorkspaceCommandType =
  | "presentation.edit"
  | "presentation.delete"
  | "slide.edit"
  | "slide.regenerate"
  | "slide.delete"
  | "version.restore"
  | "template.apply"
  | "quality.review"
  | "export.pptx"
  | "export.pdf";

export interface WorkspaceCommandRequest {
  command: string;
  commandType?: WorkspaceCommandType;
  idempotencyKey: string;
  expectedRevision?: number;
  context?: Record<string, unknown>;
  assetIds?: string[];
}

export interface WorkspaceCommandOptions {
  commandType?: WorkspaceCommandType;
  context?: Record<string, unknown>;
  assetIds?: string[];
}

export type WorkspaceCommandDispatcher = (
  command: string,
  options?: WorkspaceCommandOptions,
) => Promise<void>;

/**
 * A durable operation completion that changed the persisted presentation.
 * `fixedSlideIndexes` deliberately uses the editor's zero-based slide indexes.
 */
export interface WorkspacePresentationChange {
  operationId: string;
  operationType: string;
  result?: Record<string, unknown>;
  resultRevision?: number;
  fixedSlideIndexes?: number[];
}

export type WorkspacePresentationChangedHandler = (
  change: WorkspacePresentationChange,
) => void | Promise<void>;

/** Client-side status of applying a committed revision to the editor canvas. */
export type WorkspacePresentationRefreshState =
  | "refreshing"
  | "refreshed"
  | "failed";

export type WorkspaceQualityReviewRequester = (
  currentSlide?: number,
) => void;

export type WorkspacePageRemakeRequester = (currentSlide: number) => void;

export interface WorkspaceOperationActionRequest {
  action: "retry" | "cancel" | "approve" | "reject" | "undo";
  idempotencyKey: string;
  expectedRevision?: number;
  expectedOperationVersion?: number;
}
