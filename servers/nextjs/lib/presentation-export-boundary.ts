import {
  executePersistedPresentationExport,
  type PresentationExportAdapterRegistry,
  type PresentationExportExecutionParams,
  type PresentationExportResult,
  type PersistedPresentation,
  PRESENTATION_SOURCE_SHA256,
} from "./presentation-export-strategy.ts";

export interface ExportBoundaryDependencies<
  Result extends PresentationExportResult = PresentationExportResult,
> {
  fetchPresentation(
    presentationId: string,
    cookieHeader: string
  ): Promise<PersistedPresentation>;
  registry: PresentationExportAdapterRegistry<Result>;
}

/** Fetch persisted identity, resolve it, and invoke exactly one existing runner. */
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
  return executePersistedPresentationExport(
    presentation,
    {
      ...params,
      expectedPresentationSha256:
        presentation[PRESENTATION_SOURCE_SHA256] ??
        params.expectedPresentationSha256,
    },
    dependencies.registry
  );
}
