import {
  createRenderPresentationSnapshot,
  RenderService,
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
  });
}
