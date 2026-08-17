import type { AuthoredVnextFlowState, LegacyFlowState, PresentationFlowState } from "./types.ts";

export const AUTHORED_VNEXT_FLOW_FEATURE_FLAG =
  "NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED" as const;

/**
 * A deliberately narrow local harness override. The durable product contract
 * remains a 90-slide deck; accepting arbitrary public values here would make
 * a development convenience into a second production contract.
 */
export const AUTHORED_VNEXT_DEV_SLIDE_COUNT_FEATURE_FLAG =
  "NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT" as const;

export function isAuthoredVnextFlowEnabled(
  value = process.env.NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED,
): boolean {
  return value === "true";
}

export function authoredVnextSlideCount(
  value = process.env.NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT,
  nodeEnvironment = process.env.NODE_ENV,
): 3 | 90 {
  return nodeEnvironment !== "production" && value === "3" ? 3 : 90;
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
