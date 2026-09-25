export type ConversationWorkspacePane = "slides" | "chat";

export interface ConversationWorkspacePreferences {
  chatCollapsed: boolean;
  /** Present only after the user explicitly resizes the chat pane. */
  chatWidth?: number;
  activePane: ConversationWorkspacePane;
  lastHandledCompletionKey?: string;
}

const PREFERENCES_VERSION = 1;

interface StoredConversationWorkspacePreferences
  extends ConversationWorkspacePreferences {
  version: number;
}

function isPane(value: unknown): value is ConversationWorkspacePane {
  return value === "slides" || value === "chat";
}

function isStoredPreferences(
  value: unknown,
): value is StoredConversationWorkspacePreferences {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<StoredConversationWorkspacePreferences>;
  return (
    candidate.version === PREFERENCES_VERSION &&
    typeof candidate.chatCollapsed === "boolean" &&
    (candidate.chatWidth === undefined ||
      (typeof candidate.chatWidth === "number" &&
        Number.isFinite(candidate.chatWidth))) &&
    isPane(candidate.activePane) &&
    (candidate.lastHandledCompletionKey === undefined ||
      typeof candidate.lastHandledCompletionKey === "string")
  );
}

export function conversationWorkspaceStorageKey(
  presentationId: string,
  prefix = "presenton:conversation-workspace:v1",
): string {
  return `${prefix}:${presentationId}`;
}

export function readConversationWorkspacePreferences(
  presentationId: string,
  prefix?: string,
): ConversationWorkspacePreferences | undefined {
  if (typeof window === "undefined" || !presentationId) return undefined;
  try {
    const stored = window.sessionStorage.getItem(
      conversationWorkspaceStorageKey(presentationId, prefix),
    );
    if (!stored) return undefined;
    const parsed: unknown = JSON.parse(stored);
    if (!isStoredPreferences(parsed)) return undefined;
    return {
      chatCollapsed: parsed.chatCollapsed,
      chatWidth: parsed.chatWidth,
      activePane: parsed.activePane,
      lastHandledCompletionKey: parsed.lastHandledCompletionKey,
    };
  } catch {
    return undefined;
  }
}

export function writeConversationWorkspacePreferences(
  presentationId: string,
  preferences: ConversationWorkspacePreferences,
  prefix?: string,
): void {
  if (typeof window === "undefined" || !presentationId) return;
  try {
    const stored: StoredConversationWorkspacePreferences = {
      version: PREFERENCES_VERSION,
      ...preferences,
    };
    window.sessionStorage.setItem(
      conversationWorkspaceStorageKey(presentationId, prefix),
      JSON.stringify(stored),
    );
  } catch {
    // Browsers may deny session storage. The in-memory choices still apply.
  }
}
