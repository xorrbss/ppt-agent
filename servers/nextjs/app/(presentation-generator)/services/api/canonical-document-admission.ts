import { getHeader } from "./header";
import { getApiUrl } from "@/utils/api";
import { fetchWithNetworkRetry } from "@/utils/fetchWithNetworkRetry";

const pendingCanonicalAdmissions = new Map<string, string[]>();

async function sha256Hex(value: ArrayBuffer | string): Promise<string> {
  const bytes =
    typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function canonicalUploadBatchKey(
  documents: File[],
): Promise<string> {
  const identities = await Promise.all(
    documents.map(async (document) => ({
      contentSha256: await sha256Hex(await document.arrayBuffer()),
      lastModified: document.lastModified,
      name: document.name,
      size: document.size,
      type: document.type,
    })),
  );
  return sha256Hex(JSON.stringify(identities));
}

export function rememberCanonicalAdmission(
  referenceSnapshotId: string,
  assetIds: string[],
): void {
  pendingCanonicalAdmissions.set(referenceSnapshotId, [...assetIds]);
}

export function forgetCanonicalAdmission(referenceSnapshotId: string): void {
  pendingCanonicalAdmissions.delete(referenceSnapshotId);
}

async function compensate(url: string): Promise<void> {
  try {
    const response = await fetchWithNetworkRetry(url, {
      method: "DELETE",
      headers: getHeader(),
      cache: "no-store",
      keepalive: true,
    });
    if (!response.ok && ![404, 409].includes(response.status)) {
      console.warn("Canonical admission compensation was rejected", {
        status: response.status,
      });
    }
  } catch (error) {
    console.warn("Canonical admission compensation failed", error);
  }
}

export async function cleanupCanonicalDocumentAdmission({
  referenceSnapshotId,
  assetIds,
}: {
  referenceSnapshotId?: string;
  assetIds?: string[];
}): Promise<void> {
  const knownAssetIds = Array.from(
    new Set([
      ...(assetIds ?? []),
      ...(referenceSnapshotId
        ? pendingCanonicalAdmissions.get(referenceSnapshotId) ?? []
        : []),
    ]),
  );
  if (referenceSnapshotId) {
    await compensate(
      getApiUrl(
        `/api/v1/ppt/references/snapshots/${encodeURIComponent(referenceSnapshotId)}`,
      ),
    );
    forgetCanonicalAdmission(referenceSnapshotId);
  }
  await Promise.allSettled(
    knownAssetIds.map((assetId) =>
      compensate(
        getApiUrl(
          `/api/v1/ppt/assets/documents/${encodeURIComponent(assetId)}`,
        ),
      ),
    ),
  );
}
