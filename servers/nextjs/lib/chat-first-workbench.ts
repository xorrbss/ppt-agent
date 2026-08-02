const ENABLED_VALUES = new Set(["1", "true", "on"]);

export interface ChatFirstCapabilityContract {
  supported: boolean;
  timeline: boolean;
  commands: boolean;
  events: boolean;
  reason?: string;
}

export type ChatFirstRolloutMode = "legacy" | "checking" | "chat" | "blocked";

export interface ChatFirstRolloutDecision {
  mode: ChatFirstRolloutMode;
  reason?: string;
}

export type PresentationCreateThemeStrategy =
  | "embedded"
  | "legacy-patch"
  | "blocked";

/**
 * Chat-first is deliberately opt-in until the workspace persistence and
 * runner capabilities are available on the connected API.
 */
export function isChatFirstWorkbenchEnabled(
  value = process.env.NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED,
): boolean {
  return typeof value === "string" && ENABLED_VALUES.has(value.trim().toLowerCase());
}

/**
 * Resolve the build-time cohort flag together with the authenticated backend
 * capability. A build that contains chat-first must fail closed until the
 * runtime answers. The only safe no-rebuild rollback is the backend's explicit
 * feature-disabled response; unsupported or unhealthy runtimes must never
 * silently regain legacy mutation paths.
 */
export function resolveChatFirstWorkbenchRollout(
  buildEnabled: boolean,
  capability?: ChatFirstCapabilityContract,
): ChatFirstRolloutDecision {
  if (!buildEnabled) return { mode: "legacy", reason: "build_flag_disabled" };
  if (!capability) return { mode: "checking" };
  if (capability.reason === "chat_first_feature_disabled") {
    return { mode: "legacy", reason: capability.reason };
  }
  if (
    capability.supported &&
    capability.timeline &&
    capability.commands &&
    capability.events
  ) {
    return { mode: "chat" };
  }
  return { mode: "blocked", reason: capability.reason ?? "capability_incomplete" };
}

/** Keep upload theme handling aligned with the selected mutation contract. */
export function resolvePresentationCreateThemeStrategy(
  mode: ChatFirstRolloutMode,
): PresentationCreateThemeStrategy {
  if (mode === "chat") return "embedded";
  if (mode === "legacy") return "legacy-patch";
  return "blocked";
}
