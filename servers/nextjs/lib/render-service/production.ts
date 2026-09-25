import { decodeBase64, encodeBase64, sha256Hex } from "./isomorphic-sha256.ts";
import type { RenderArtifact } from "./contracts.ts";
import { createProductionRenderArtifactCore, type ProductionRenderArtifactOptions } from "./production-core.ts";

export { renderArtifactSlides } from "./production-core.ts";
export type { ProductionRenderArtifactOptions } from "./production-core.ts";

/**
 * Server entry point preserves the synchronous production render contract.
 * Inline scene assets are verified with the isomorphic helpers rather than
 * `node:crypto`/`Buffer`, so this module stays bundle-safe if a client path
 * ever imports it (see 0b8a2a5d6).
 */
export function createProductionRenderArtifact(
  presentation: unknown,
  options: ProductionRenderArtifactOptions,
): RenderArtifact {
  return createProductionRenderArtifactCore(presentation, options, (encoded, digest, sizeBytes) => {
    const bytes = decodeBase64(encoded);
    return bytes !== null &&
      bytes.length === sizeBytes &&
      encodeBase64(bytes) === encoded &&
      sha256Hex(bytes) === digest;
  });
}
