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

export interface WorkspaceAttachment {
  id: string;
  name: string;
  status: string;
  mimeType?: string;
  size?: number;
  error?: string;
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
  type: string;
  status?: string;
  message?: string;
  progress?: number;
  error?: string;
  retryable?: boolean;
  result?: Record<string, unknown>;
  createdAt?: string;
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
  createdAt?: string;
  updatedAt?: string;
  events: WorkspaceOperationEvent[];
}

export interface WorkspaceUnknownAnchor {
  anchorType: "unknown";
  id: string;
  position: number;
  rawType: string;
}

export type WorkspaceTimelineAnchor =
  | WorkspaceMessageAnchor
  | WorkspaceOperationAnchor
  | WorkspaceUnknownAnchor;

export interface WorkspaceTimelinePage {
  items: WorkspaceTimelineAnchor[];
  nextBeforePosition?: number;
  eventHighWatermark?: string;
  hasMore: boolean;
  revision?: number;
}

export interface WorkspaceEventPage {
  events: WorkspaceOperationEvent[];
  eventHighWatermark?: string;
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
  | "slide.edit"
  | "slide.regenerate"
  | "slide.delete"
  | "version.restore"
  | "quality.review"
  | "export.pptx"
  | "export.pdf";

export interface WorkspaceCommandRequest {
  command: string;
  commandType?: WorkspaceCommandType;
  idempotencyKey: string;
  expectedRevision?: number;
  context?: Record<string, unknown>;
}

export interface WorkspaceCommandOptions {
  commandType?: WorkspaceCommandType;
  context?: Record<string, unknown>;
}

export type WorkspaceCommandDispatcher = (
  command: string,
  options?: WorkspaceCommandOptions,
) => Promise<void>;

export interface WorkspaceOperationActionRequest {
  action: "retry" | "cancel" | "approve";
  idempotencyKey: string;
  expectedRevision?: number;
  expectedOperationVersion?: number;
}
