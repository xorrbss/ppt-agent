/**
 * Artifact-path safety and the completed/in-flight result caches for the
 * authored-hybrid PPTX export.
 *
 * Split out of `export.ts` to keep that module within the repository
 * file-size budget. Move only -- the caches keep module-singleton identity
 * because `export.ts` is their sole importer.
 */

import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";

import { resolveAppDataDirectory } from "@/lib/app-data-directory";
import type { BundledPresentationExportResult } from "@/lib/run-bundled-presentation-export";

const MAX_FIDELITY_PPTX_BYTES = 512 * 1024 * 1024;
const HYBRID_CACHE_VERSION =
  "authored-hybrid-v23-local-fit-capture-evidence";
const HYBRID_RESULT_CACHE_LIMIT = 24;

interface CompletedHybridExport {
  result: BundledPresentationExportResult;
  artifactSha256: string;
  artifactSizeBytes: number;
}

const completedHybridExports = new Map<string, CompletedHybridExport>();
export const inFlightHybridExports = new Map<
  string,
  Promise<BundledPresentationExportResult>
>();

export async function resolveSafeFidelityPptx(outPath: string): Promise<string> {
  if (path.extname(outPath).toLowerCase() !== ".pptx") {
    throw new Error("Fidelity export did not produce a PPTX file.");
  }
  const exportsDirectory = await fs.realpath(
    path.join(resolveAppDataDirectory(), "exports"),
  );
  const resolved = await fs.realpath(outPath);
  if (
    resolved === exportsDirectory ||
    !resolved.startsWith(exportsDirectory + path.sep)
  ) {
    throw new Error("Fidelity export finished outside the exports directory.");
  }
  const stats = await fs.stat(resolved);
  if (
    !stats.isFile() ||
    stats.size < 22 ||
    stats.size > MAX_FIDELITY_PPTX_BYTES
  ) {
    throw new Error("Fidelity PPTX is not a safe readable file.");
  }
  return resolved;
}

export function createHybridCacheKey(
  presentationId: string,
  sourceSha256: string,
  sourceRevision: number | undefined,
  fontEmbeddingDiscriminator: string,
  exportAttemptId: string | undefined,
  canonicalSourceDigest: string | undefined,
  editableHybridProfileId: string | undefined,
): string {
  return createHash("sha256")
    .update(HYBRID_CACHE_VERSION)
    .update("\0")
    .update(presentationId)
    .update("\0")
    .update(sourceSha256)
    .update("\0")
    .update(sourceRevision === undefined ? "unknown" : String(sourceRevision))
    .update("\0")
    .update(fontEmbeddingDiscriminator)
    .update("\0")
    .update(exportAttemptId ?? "unbound")
    .update("\0")
    .update(canonicalSourceDigest ?? "unbound")
    .update("\0")
    .update(editableHybridProfileId ?? "raster-only")
    .digest("hex");
}

export async function readHybridArtifactIdentity(
  artifactPath: string,
): Promise<{ sha256: string; sizeBytes: number }> {
  const safePath = await resolveSafeFidelityPptx(artifactPath);
  const stats = await fs.stat(safePath);
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(safePath)) {
    digest.update(chunk);
  }
  return { sha256: digest.digest("hex"), sizeBytes: stats.size };
}

export async function rememberCompletedHybridExport(
  cacheKey: string,
  result: BundledPresentationExportResult,
): Promise<void> {
  const identity = await readHybridArtifactIdentity(result.path);
  completedHybridExports.delete(cacheKey);
  completedHybridExports.set(cacheKey, {
    result,
    artifactSha256: identity.sha256,
    artifactSizeBytes: identity.sizeBytes,
  });
  while (completedHybridExports.size > HYBRID_RESULT_CACHE_LIMIT) {
    const oldestKey = completedHybridExports.keys().next().value;
    if (typeof oldestKey !== "string") break;
    completedHybridExports.delete(oldestKey);
  }
}

export async function readCompletedHybridExport(
  cacheKey: string,
): Promise<BundledPresentationExportResult | null> {
  const cached = completedHybridExports.get(cacheKey);
  if (!cached) return null;
  try {
    const safePath = await resolveSafeFidelityPptx(cached.result.path);
    const identity = await readHybridArtifactIdentity(safePath);
    if (
      identity.sha256 !== cached.artifactSha256 ||
      identity.sizeBytes !== cached.artifactSizeBytes
    ) {
      throw new Error("cached_hybrid_artifact_digest_mismatch");
    }
    completedHybridExports.delete(cacheKey);
    const safeResult = { ...cached.result, path: safePath };
    completedHybridExports.set(cacheKey, { ...cached, result: safeResult });
    return safeResult;
  } catch {
    completedHybridExports.delete(cacheKey);
    return null;
  }
}
