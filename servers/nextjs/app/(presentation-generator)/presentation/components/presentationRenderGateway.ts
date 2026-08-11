import {
  createRenderPresentationSnapshot,
  RenderService,
  createProductionRenderArtifact,
  type RenderArtifact,
  type RenderRequest,
  type RenderTarget,
} from "../../../../lib/render-service/index.ts";

export type PresentationRenderAction = "preview" | "pdf" | "pptx";

/**
 * UI composition does not render from client-selected modes.  These factories
 * create a revision-bound RenderArtifact through the canonical gateway; the
 * transport layer can send the artifact to preview/download adapters.
 */
export function createPresentationRenderActionFactory(
  service: Pick<RenderService, "render"> = new RenderService(),
) {
  return Object.freeze({
    render(
      action: PresentationRenderAction,
      presentation: unknown,
      options: Omit<RenderRequest, "target" | "snapshot" | "expected_revision"> = {},
    ): RenderArtifact {
      const snapshot = createRenderPresentationSnapshot(presentation);
      const target: RenderTarget = action;
      return service.render({
        ...options,
        target,
        snapshot,
        expected_revision: snapshot.source.revision,
      });
    },
    async renderProduction(
      action: PresentationRenderAction,
      presentation: unknown,
      options: Pick<
        RenderRequest,
        "expected_artifact_version" | "template_render_plans"
      > = {},
    ): Promise<RenderArtifact> {
      const encoded = new TextEncoder().encode(JSON.stringify(presentation));
      const digest = await globalThis.crypto.subtle.digest("SHA-256", encoded);
      const sourceSha256 = Array.from(new Uint8Array(digest), (value) =>
        value.toString(16).padStart(2, "0"),
      ).join("");
      return createProductionRenderArtifact(presentation, {
        target: action,
        source_sha256: sourceSha256,
        service,
        expected_artifact_version: options.expected_artifact_version,
        request: {
          template_render_plans: options.template_render_plans,
        },
      });
    },
  });
}
