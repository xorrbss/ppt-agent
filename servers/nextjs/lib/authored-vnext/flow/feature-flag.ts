import type { AuthoredVnextFlowState, LegacyFlowState, PresentationFlowState } from "./types.ts";

export const AUTHORED_VNEXT_FLOW_FEATURE_FLAG =
  "NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED" as const;

export function isAuthoredVnextFlowEnabled(
  value = process.env.NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED,
): boolean {
  return value === "true";
}

/**
 * The disabled branch deliberately returns the caller-owned legacy value by
 * reference. The vNext model therefore cannot rewrite or partially hydrate it.
 */
export function selectPresentationFlow<TLegacy>(
  legacy: TLegacy,
  createAuthoredVnext: () => AuthoredVnextFlowState,
  flagValue = process.env.NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED,
): PresentationFlowState<TLegacy> {
  if (!isAuthoredVnextFlowEnabled(flagValue)) {
    const state: LegacyFlowState<TLegacy> = {
      mode: "legacy",
      featureEnabled: false,
      legacy,
    };
    return state;
  }
  return createAuthoredVnext();
}
