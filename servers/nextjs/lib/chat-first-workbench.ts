const ENABLED_VALUES = new Set(["1", "true", "on"]);

export interface ChatFirstCapabilityContract {
  supported: boolean;
  timeline: boolean;
  commands: boolean;
  events: boolean;
  reason?: string;
}

export type ChatFirstRolloutMode = "legacy" | "checking" | "chat" | "blocked";

const EXPLICIT_LEGACY_ROLLBACK_REASONS = new Set([
  "chat_first_feature_disabled",
  "chat_first_kill_switch_enabled",
  "chat_first_cohort_excluded",
]);

export function isExplicitChatFirstLegacyRollback(
  capability?: Partial<ChatFirstCapabilityContract>,
): boolean {
  return (
    typeof capability?.reason === "string" &&
    EXPLICIT_LEGACY_ROLLBACK_REASONS.has(capability.reason)
  );
}

export interface ChatFirstRolloutDecision {
  mode: ChatFirstRolloutMode;
  reason?: string;
}

/** Read-only history remains available during an explicit write rollback. */
export function canReadChatFirstWorkspace(
  capability?: Partial<ChatFirstCapabilityContract>,
): boolean {
  return Boolean(
    capability?.supported && capability.timeline && capability.events,
  );
}

/** Validate a same-origin tab broadcast before it can replace local rollout state. */
export function shouldApplyChatFirstCapabilityBroadcast(
  value: unknown,
  expectedKey: string,
  currentCheckedAt: number,
  currentCapability?: ChatFirstCapabilityContract,
): boolean {
  if (!value || typeof value !== "object") return false;
  const candidate = value as {
    kind?: unknown;
    key?: unknown;
    snapshot?: {
      checkedAt?: unknown;
      capability?: Partial<ChatFirstCapabilityContract>;
    };
  };
  const capability = candidate.snapshot?.capability;
  const checkedAt = candidate.snapshot?.checkedAt;
  const structurallyValid =
    candidate.kind === "capability" &&
    candidate.key === expectedKey &&
    typeof checkedAt === "number" &&
    Number.isFinite(checkedAt) &&
    checkedAt >= currentCheckedAt &&
    !!capability &&
    typeof capability.supported === "boolean" &&
    typeof capability.timeline === "boolean" &&
    typeof capability.commands === "boolean" &&
    typeof capability.events === "boolean";
  if (!structurallyValid) return false;
  // A tab cannot lift a server rollback in another tab. Only a fresh server
  // response may do that, while rollback responses remain safety-dominant.
  return !(
    isExplicitChatFirstLegacyRollback(currentCapability) &&
    !isExplicitChatFirstLegacyRollback(capability)
  );
}

/** Do not let a stale in-flight capability response replace newer rollout state. */
export function shouldCommitChatFirstCapabilityRequest(
  startedAtGeneration: number,
  currentGeneration: number,
  capability?: ChatFirstCapabilityContract,
): boolean {
  return (
    startedAtGeneration === currentGeneration ||
    isExplicitChatFirstLegacyRollback(capability)
  );
}

export type PresentationCreateThemeStrategy =
  "embedded" | "legacy-patch" | "blocked";

/**
 * Chat-first is deliberately opt-in until the workspace persistence and
 * runner capabilities are available on the connected API.
 */
export function isChatFirstWorkbenchEnabled(
  value = process.env.NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED,
): boolean {
  return (
    typeof value === "string" && ENABLED_VALUES.has(value.trim().toLowerCase())
  );
}

/**
 * Resolve the build-time cohort flag together with the authenticated backend
 * capability. A build that contains chat-first must fail closed until the
 * runtime answers. Explicit backend feature-off, kill-switch, and cohort
 * exclusion responses restore the legacy surface without a rebuild;
 * unsupported or unhealthy runtimes must never silently regain legacy writes.
 */
export function resolveChatFirstWorkbenchRollout(
  buildEnabled: boolean,
  capability?: ChatFirstCapabilityContract,
): ChatFirstRolloutDecision {
  if (!buildEnabled) return { mode: "legacy", reason: "build_flag_disabled" };
  if (!capability) return { mode: "checking" };
  if (isExplicitChatFirstLegacyRollback(capability)) {
    return { mode: "legacy", reason: capability.reason };
  }
  if (capability.supported && capability.timeline && capability.events) {
    return { mode: "chat" };
  }
  return {
    mode: "blocked",
    reason: capability.reason ?? "capability_incomplete",
  };
}

/** Keep upload theme handling aligned with the selected mutation contract. */
export function resolvePresentationCreateThemeStrategy(
  mode: ChatFirstRolloutMode,
): PresentationCreateThemeStrategy {
  if (mode === "chat") return "embedded";
  if (mode === "legacy") return "legacy-patch";
  return "blocked";
}
