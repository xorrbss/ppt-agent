import type { RenderArtifact } from "./contracts.ts";
import {
  collectInlineSceneAssetChecks,
  createProductionRenderArtifactCore,
  type ProductionRenderArtifactOptions,
} from "./production-core.ts";
import { RenderPolicyError } from "./runtime-policy.ts";

/** Browser-only entry point: inline assets retain the server's integrity gate. */
export async function createBrowserProductionRenderArtifact(
  presentation: unknown,
  options: ProductionRenderArtifactOptions,
): Promise<RenderArtifact> {
  // Do not allow callers to mutate the manifest between verification and render.
  const source = structuredClone(presentation);
  const checks = collectInlineSceneAssetChecks(source);
  const verified = new Map<string, { digest: string; sizeBytes: number }>();
  for (const check of checks) {
    let binary: string;
    try { binary = globalThis.atob(check.encoded); }
    catch { throw new RenderPolicyError("render_scene_asset_manifest_invalid"); }
    if (binary.length !== check.sizeBytes || globalThis.btoa(binary) !== check.encoded) {
      throw new RenderPolicyError("render_scene_asset_manifest_invalid");
    }
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    const digestBuffer = await globalThis.crypto.subtle.digest("SHA-256", bytes.buffer);
    const digest = Array.from(new Uint8Array(digestBuffer), (value) => value.toString(16).padStart(2, "0")).join("");
    if (digest !== check.digest) throw new RenderPolicyError("render_scene_asset_manifest_invalid");
    verified.set(check.encoded, { digest, sizeBytes: bytes.byteLength });
  }
  return createProductionRenderArtifactCore(source, options, (encoded, digest, sizeBytes) => {
    const check = verified.get(encoded);
    return check?.digest === digest && check.sizeBytes === sizeBytes;
  });
}
