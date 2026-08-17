import type { MasterRevisionEnvelope } from "./contracts";
import type { CompanyStandardSelection } from "@/store/slices/presentationGenUpload";

// Store only the user's preferred master identity. A new outline must resolve
// the owner's current published pointer before constructing its immutable pin.
const LAST_USED_MASTER_ID_KEY = "presenton:authored-master:last-used-id:v1";
const EXPLICITLY_CLEARED_MASTER_KEY = "presenton:authored-master:explicitly-cleared:v1";

// Temporary client-side workspace default until the workspace preference API is
// available. The id is resolved to a published head every time and a 404 leaves
// the selection empty, so this never invents a pin or revives a deleted master.
export const DEFAULT_WORKSPACE_COMPANY_STANDARD_ID = "master:ibiz-proposal-blue";

export type StoredCompanyStandardPreference =
  | { kind: "explicit-clear" }
  | { kind: "last-used"; masterId: string }
  | { kind: "none" };

export function companyStandardDisplayName(
  name: string,
  masterId?: string | null,
): string {
  if (masterId === DEFAULT_WORKSPACE_COMPANY_STANDARD_ID) {
    return "IBIZ 회사 표준";
  }
  const trimmed = name.trim() || "회사 표준";
  return /회사\s*표준/i.test(trimmed) ? trimmed : `${trimmed} 회사 표준`;
}

export const masterSelectionFromPublished = (
  published: MasterRevisionEnvelope,
): CompanyStandardSelection => {
  const contract = published.contract;
  return {
    masterId: published.masterId,
    revision: published.revision,
    digest: published.digest,
    name: companyStandardDisplayName(
      contract?.name || "회사 표준",
      published.masterId,
    ),
    hasLogo: Boolean(contract?.brandAssets.logo || contract?.brandAssets.wordmark),
    primaryFont:
      contract?.fontManifest.primary || contract?.tokens.fonts.eastAsian || "",
    layoutCount: contract
      ? Object.keys(contract.layouts).length + (contract.customLayouts?.length ?? 0)
      : 0,
  };
};

export function readStoredCompanyStandardPreference(): StoredCompanyStandardPreference {
  if (typeof window === "undefined") return { kind: "none" };
  try {
    if (window.localStorage.getItem(EXPLICITLY_CLEARED_MASTER_KEY) === "1") {
      return { kind: "explicit-clear" };
    }
    const value = window.localStorage.getItem(LAST_USED_MASTER_ID_KEY)?.trim();
    return value?.startsWith("master:")
      ? { kind: "last-used", masterId: value }
      : { kind: "none" };
  } catch {
    return { kind: "none" };
  }
}

export function loadLastUsedMasterId(): string | null {
  const preference = readStoredCompanyStandardPreference();
  return preference.kind === "last-used" ? preference.masterId : null;
}

export function saveLastUsedMasterId(masterId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LAST_USED_MASTER_ID_KEY, masterId);
    window.localStorage.removeItem(EXPLICITLY_CLEARED_MASTER_KEY);
  } catch (error) {
    console.warn("Unable to persist the last used PPT master.", error);
  }
}

export function clearLastUsedMasterId(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(LAST_USED_MASTER_ID_KEY);
    // Clear is an explicit design intent, not the absence of a remembered
    // preference. Retain it so the workspace default is not silently restored.
    window.localStorage.setItem(EXPLICITLY_CLEARED_MASTER_KEY, "1");
  } catch (error) {
    console.warn("Unable to clear the last used PPT master.", error);
  }
}

export const generationMasterPin = (selection: CompanyStandardSelection) => ({
  masterId: selection.masterId,
  revision: selection.revision,
  digest: selection.digest,
});
