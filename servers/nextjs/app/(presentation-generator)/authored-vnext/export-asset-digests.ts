import type { DeckMasterContractV1 } from "@/lib/authored-vnext/master/contracts";

/** Digest pins for all master-owned assets that the export worker must read. */
export function masterAssetDigests(
  contract: DeckMasterContractV1 | undefined,
): string[] {
  if (!contract) return [];
  const brand = [contract.brandAssets.logo, contract.brandAssets.wordmark]
    .filter((asset) => asset !== null)
    .map((asset) => asset.digest);
  const semantic = (contract.semanticElements ?? []).flatMap((element) =>
    element.type === "image" && element.content.assetId && element.content.digest
      ? [element.content.digest]
      : [],
  );
  return [...new Set([...brand, ...semantic])];
}
