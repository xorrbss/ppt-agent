import {
  executePersistedPresentationExport,
  type PresentationExportAdapterRegistry,
  type PresentationExportExecutionParams,
  type PresentationExportResult,
  type PersistedPresentation,
  PRESENTATION_SOURCE_SHA256,
} from "./presentation-export-strategy.ts";
import {
  assertRenderArtifactFresh,
  createProductionRenderArtifact,
  type RenderArtifact,
} from "./render-service/index.ts";

export interface ExportBoundaryDependencies<
  Result extends PresentationExportResult = PresentationExportResult,
> {
  fetchPresentation(
    presentationId: string,
    cookieHeader: string
  ): Promise<PersistedPresentation>;
  registry: PresentationExportAdapterRegistry<Result>;
}

function assertMatchingArtifact(
  expected: RenderArtifact,
  actual: RenderArtifact,
): void {
  assertRenderArtifactFresh(actual, expected.source);
  if (
    actual.target !== expected.target ||
    actual.driver_id !== expected.driver_id ||
    actual.artifact_version !== expected.artifact_version ||
    actual.renderer_version !== expected.renderer_version
  ) {
    throw new Error("render_artifact_execution_mismatch");
  }
}

/**
 * Fetch persisted identity, create the canonical render artifact, and invoke
 * exactly one existing runner. A path-only runner is retained only as the
 * documented rolling facade; this boundary always returns a validated
 * revision/digest-bound artifact to canonical callers.
 */
export async function executeExportAtProductionBoundary<
  Result extends PresentationExportResult,
>(
  params: PresentationExportExecutionParams,
  dependencies: ExportBoundaryDependencies<Result>
): Promise<Result> {
  const presentation = await dependencies.fetchPresentation(
    params.presentationId,
    params.cookieHeader ?? ""
  );
  const sourceSha256 = presentation[PRESENTATION_SOURCE_SHA256];
  if (
    sourceSha256 &&
    params.expectedPresentationSha256 !== undefined &&
    params.expectedPresentationSha256.toLowerCase() !== sourceSha256
  ) {
    throw new Error("stale_render_source_digest");
  }
  if (
    params.expectedPresentationRevision !== undefined &&
    params.expectedPresentationRevision !== presentation.revision
  ) {
    throw new Error("stale_render_revision");
  }
  const attachArtifact = async (
    execute: () => Promise<Result>,
  ): Promise<Result> => {
    if (!sourceSha256) {
      throw new Error("render_artifact_source_digest_required");
    }
    const artifact = createProductionRenderArtifact(presentation, {
      target: params.format,
      source_sha256: sourceSha256,
      expected_revision:
        params.expectedPresentationRevision ?? Number(presentation.revision),
      expected_artifact_version: params.expectedArtifactVersion,
    });
    const result = await execute();
    if (result.renderArtifact) {
      assertMatchingArtifact(artifact, result.renderArtifact);
      return result;
    }
    return {
      ...result,
      renderArtifact: artifact,
    };
  };
  return executePersistedPresentationExport(
    presentation,
    {
      ...params,
      expectedPresentationRevision:
        params.expectedPresentationRevision ?? Number(presentation.revision),
      expectedPresentationSha256:
        sourceSha256 ?? params.expectedPresentationSha256,
    },
    {
      general: (executionParams) =>
        attachArtifact(() => dependencies.registry.general(executionParams)),
      ...(dependencies.registry.authoredFidelity
        ? {
            authoredFidelity: (executionParams: PresentationExportExecutionParams) =>
              attachArtifact(() =>
                dependencies.registry.authoredFidelity!(executionParams),
              ),
          }
        : {}),
      hybrid: (executionParams) =>
        attachArtifact(() => dependencies.registry.hybrid(executionParams)),
    },
  );
}
