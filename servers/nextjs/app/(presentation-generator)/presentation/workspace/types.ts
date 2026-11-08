import type { GenerationFailureClass } from "./generationFailure.ts";

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
  "slide.regenerate.batch",
  "slide.delete",
  "version.restore",
  "template.apply",
  "design.apply",
  "quality.review",
  "export.pptx",
  "export.pdf",
  "svg.chart.verify",
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
  /** Backend message key for a command the server wrote on the user's behalf. */
  contentKey?: string;
  contentParams?: Record<string, unknown>;
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
  /** Backend message key (`lib/backend-messages.ts`); `message` is its fallback. */
  messageKey?: string;
  messageParams?: Record<string, unknown>;
  progress?: number;
  stage?: string;
  current?: number;
  total?: number;
  error?: string;
  retryable?: boolean;
  /** Durable scheduler wait; capacity is not a generation error. */
  capacityWaiting?: boolean;
  /** Best-effort position among capacity-waiting operations. */
  queuePosition?: number;
  waitReason?: string;
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

export interface WorkspaceOperationTargetContext extends Record<string, unknown> {
  artifact_id?: string;
  review_target?: "source_svg" | "final_pptx";
}

export interface WorkspaceOperationTarget extends Record<string, unknown> {
  context?: WorkspaceOperationTargetContext;
}

export interface WorkspaceOperationAnchor {
  anchorType: "operation";
  id: string;
  operationId: string;
  position: number;
  operationType: string;
  title: string;
  /** `workspace.operation.<kind>`; `title` is the English fallback. */
  titleKey?: string;
  status: string;
  retryOfOperationId?: string;
  approvalExpiresAt?: string;
  version?: number;
  progress?: number;
  stage?: string;
  current?: number;
  total?: number;
  error?: string;
  /** Server classification of a terminal `presentation.generate` failure. */
  failureClass?: GenerationFailureClass;
  retryable?: boolean;
  capacityWaiting?: boolean;
  queuePosition?: number;
  waitReason?: string;
  result?: Record<string, unknown>;
  actor?: WorkspaceOperationActor;
  target?: WorkspaceOperationTarget;
  expectedRevision?: number;
  resultRevision?: number;
  /** Derived UI-only audit state. The immutable operation status is preserved. */
  historicalResolution?: "resolved" | "superseded";
  supersededByOperationId?: string;
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

export type WorkspaceAuthoredStyleIntent =
  | { intent: "keep" }
  | { intent: "set"; styleId: string };

export interface WorkspaceDesignMasterPin {
  masterId: string;
  revision: number;
  digest: string;
}

export type WorkspaceMasterIntent =
  | { intent: "keep" }
  | { intent: "set"; pin: WorkspaceDesignMasterPin }
  | { intent: "clear" };

export interface WorkspaceTemplateApplicationRequest {
  sourceRevision: number;
  prompt: string;
  authoredStyle: WorkspaceAuthoredStyleIntent;
  master: WorkspaceMasterIntent;
  visionQa: boolean;
  idempotencyKey: string;
}

export interface WorkspaceTemplateApplicationReceipt {
  schemaVersion: number;
  sourcePresentationId: string;
  sourceRevision: number;
  targetPresentationId: string;
  conversationId: string;
  firstUserMessageId: string;
  operationId: string;
  status: string;
  operationState: string;
  duplicate: boolean;
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
  | "slide.regenerate.batch"
  | "slide.delete"
  | "version.restore"
  | "template.apply"
  | "design.apply"
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
  options?: { scope?: "review_required" },
) => void;

/** A number requests one page; an array requests the batch preview dialog. */
export type WorkspacePageRemakeRequester = (
  slides: number | number[],
) => void;

export interface WorkspaceOperationActionRequest {
  action: "retry" | "cancel" | "approve" | "reject" | "undo";
  idempotencyKey: string;
  expectedRevision?: number;
  expectedOperationVersion?: number;
  recoveryRevision?: "svg-partial-recovery/v1";
  recoveryDecision?: WorkspacePartialRecoveryDecision;
}

export interface WorkspacePartialRecoveryDecision {
  manifestHash: string;
  nonce: string;
  acknowledgedUnknownIndices: number[];
  additionalBudgetMicrounits: number;
}

export interface WorkspacePartialRecoveryPlan {
  recoveryRevision: "svg-partial-recovery/v1";
  manifestHash: string;
  nonce: string;
  operationId: string;
  operationVersion: number;
  presentationId: string;
  presentationRevision: number;
  expectedPageCount: number;
  knownIndices: number[];
  newIndices: number[];
  unknownIndices: number[];
  historicalActualCostUnbounded: boolean;
  provenRemainingBudgetMicrounits: number | null;
  additionalBudgetLimitMicrounits: number;
  prospectiveAuthorRequestCount: number;
  prospectiveRetryRequestLimit: number;
}

export type WorkspaceOperationActionOptions = Partial<Omit<WorkspaceOperationActionRequest, "action">>;
