import type { MasterRevisionEnvelope } from "./contracts";
import type { CompanyStandardSelection } from "@/store/slices/presentationGenUpload";

// Legacy browser-scoped preference keys. Company-standard selection is now
// document-scoped, so new upload flows only clear these stale values.
const LAST_USED_MASTER_ID_KEY = "presenton:authored-master:last-used-id:v1";
const EXPLICITLY_CLEARED_MASTER_KEY = "presenton:authored-master:explicitly-cleared:v1";

// Compatibility display alias for the seeded IBIZ catalog entry. It is not a
// selection default and must never be applied automatically.
export const DEFAULT_WORKSPACE_COMPANY_STANDARD_ID = "master:ibiz-proposal-blue";

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
