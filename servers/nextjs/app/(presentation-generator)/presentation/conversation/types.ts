export type ConversationRole = "user" | "assistant" | "system";

export interface ConversationAsset {
  id: string;
  name: string;
  status: string;
  mimeType?: string;
  sizeBytes?: number;
}

export interface ConversationViewport {
  width?: number;
  height?: number;
  visibleSlideIds?: string[];
  zoom?: number;
}

export interface ConversationSlideReference {
  slideId?: string;
  index: number;
}

export interface SmartHtmlSelection {
  html: string;
  text?: string;
  slideId?: string;
  slideIndex?: number;
}

/** Client hints only. Generation mode and capabilities are always server derived. */
export interface ConversationContext {
  viewport?: ConversationViewport;
  currentSlide?: ConversationSlideReference;
  smartSelection?: SmartHtmlSelection;
  assetIds?: string[];
}

export interface Conversation {
  id: string;
  presentationId: string;
  revision?: number;
}

export interface ConversationMessage {
  id: string;
  role: ConversationRole;
  content: string;
  createdAt?: string;
  attachments: ConversationAsset[];
  streaming?: boolean;
}

export type AgentRunStatus =
  | "queued"
  | "waiting_for_assets"
  | "blocked"
  | "running"
  | "approval_required"
  | "awaiting_approval"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "dead_letter";

export interface AgentRun {
  id: string;
  status: AgentRunStatus;
  title?: string;
  version?: number;
  error?: string;
  expectedRevision?: number;
  resultRevision?: number;
  actions?: ConversationRunAction[];
}

export type ConversationRunAction =
  | "retry"
  | "cancel"
  | "approve"
  | "reject"
  | "undo";

export interface ConversationToolActivity {
  id: string;
  runId: string;
  name: string;
  status: "started" | "completed";
  summary?: string;
}

export type ConversationTimelineItem =
  | { kind: "message"; position: number; message: ConversationMessage }
  | { kind: "run"; position: number; run: AgentRun }
  | { kind: "tool"; position: number; tool: ConversationToolActivity };

export interface ConversationTimeline {
  items: ConversationTimelineItem[];
  revision?: number;
  cursor?: string;
  hasMore: boolean;
  nextBeforePosition?: number;
}

export type ConversationEventType =
  | "message.created"
  | "run.queued"
  | "run.started"
  | "run.status"
  | "assistant.delta"
  | "assistant.checkpoint"
  | "tool.started"
  | "tool.completed"
  | "approval.required"
  | "presentation.published"
  | "run.completed"
  | "run.failed";

export interface ConversationEvent {
  id: string;
  cursor?: string;
  type: ConversationEventType | string;
  runId?: string;
  messageId?: string;
  status?: AgentRunStatus;
  delta?: string;
  checkpoint?: string;
  message?: ConversationMessage;
  run?: AgentRun;
  tool?: ConversationToolActivity;
  revision?: number;
  createdAt?: string;
}

export interface SendConversationMessageRequest {
  content: string;
  idempotencyKey: string;
  expectedRevision?: number;
  context?: ConversationContext;
  assetIds?: string[];
  /** Server-allowlisted operation intent for non-composer workspace controls. */
  commandType?: string;
  operationContext?: Record<string, unknown>;
}

export interface ConversationCommandOptions {
  commandType?: string;
  context?: Record<string, unknown>;
  assetIds?: string[];
}

export type ConversationCommandDispatcher = (
  content: string,
  options?: ConversationCommandOptions,
) => Promise<void>;

export interface ConversationTransport {
  getOrCreate(presentationId: string, signal?: AbortSignal): Promise<Conversation>;
  getTimeline(
    conversationId: string,
    options?: { beforePosition?: number; signal?: AbortSignal },
  ): Promise<ConversationTimeline>;
  sendMessage(
    conversationId: string,
    request: SendConversationMessageRequest,
    signal?: AbortSignal,
  ): Promise<ConversationTimeline>;
  performRunAction(
    conversationId: string,
    runId: string,
    action: ConversationRunAction,
    request: { idempotencyKey: string; expectedRevision?: number; expectedRunVersion?: number },
    signal?: AbortSignal,
  ): Promise<ConversationTimeline>;
  streamEvents(
    conversationId: string,
    options: { after?: string; signal?: AbortSignal; onEvent: (event: ConversationEvent) => void },
  ): Promise<void>;
  uploadAsset?(conversationId: string, file: File, signal?: AbortSignal): Promise<ConversationAsset>;
}
