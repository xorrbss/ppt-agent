import { createHash } from "node:crypto";
import type { RenderArtifact } from "./contracts.ts";
import { createProductionRenderArtifactCore, type ProductionRenderArtifactOptions } from "./production-core.ts";

export { renderArtifactSlides } from "./production-core.ts";
export type { ProductionRenderArtifactOptions } from "./production-core.ts";

/** Server entry point preserves the synchronous production render contract. */
export function createProductionRenderArtifact(
  presentation: unknown,
  options: ProductionRenderArtifactOptions,
): RenderArtifact {
  return createProductionRenderArtifactCore(presentation, options, (encoded, digest, sizeBytes) => {
    const bytes = Buffer.from(encoded, "base64");
    return bytes.length === sizeBytes &&
      bytes.toString("base64") === encoded &&
      createHash("sha256").update(bytes).digest("hex") === digest;
  });
}
