import type {
  RenderArtifact,
  RenderSourceIdentity,
} from "./contracts.ts";
import { RenderPolicyError } from "./runtime-policy.ts";

export function buildRenderArtifactVersion(
  source: RenderSourceIdentity,
  rendererVersion: string,
): string {
  const digest = source.source_sha256 ?? "no-digest";
  return `${rendererVersion}/revision-${source.revision}/${source.lifecycle_status}/${digest}`;
}

export function assertRenderArtifactFresh(
  artifact: RenderArtifact,
  current: RenderSourceIdentity,
): void {
  if (
    artifact.source.presentation_id !== current.presentation_id ||
    artifact.source.revision !== current.revision ||
    artifact.source.lifecycle_status !== current.lifecycle_status ||
    artifact.source.source_sha256 !== current.source_sha256 ||
    artifact.artifact_version !==
      buildRenderArtifactVersion(current, artifact.renderer_version)
  ) {
    throw new RenderPolicyError("stale_render_artifact");
  }
}
