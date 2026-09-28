import { recordOrNull } from "@/lib/guards";
/**
 * Compare-and-set pre-conditions for applying a company standard to a
 * presentation that already exists. Extracted from OutlinePage so the page
 * stays inside the file-size gate while it carries view concerns only.
 */
export const record = recordOrNull;

export const existingMasterApplyCas = (presentation: unknown) => {
  const data = record(presentation);
  const metadata = record(record(data?.theme)?.authored_vnext);
  const presentationRevision = data?.revision;
  const deckRevision = metadata?.deckRevision;
  const deckDigest = metadata?.deckDigest;
  if (
    !Number.isInteger(presentationRevision) ||
    !Number.isInteger(deckRevision) ||
    typeof deckDigest !== "string" ||
    !deckDigest.startsWith("sha256:")
  ) {
    return null;
  }
  return {
    presentationRevision: Number(presentationRevision),
    deckRevision: Number(deckRevision),
    deckDigest: deckDigest as `sha256:${string}`,
  };
};
