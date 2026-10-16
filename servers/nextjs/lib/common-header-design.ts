export interface CommonHeaderDesign {
  instructions?: string;
  reference_snapshot_ids?: string[];
  document_values?: Record<string, string>;
  page_values?: Array<{ page_index: number; values: Record<string, string> }>;
}

export function commonHeaderDesignForConfig(config: {
  commonHeaderMode?: "uniform" | "free";
  commonHeaderDesign?: CommonHeaderDesign;
  svgDesignReferenceSnapshotId?: string;
}): CommonHeaderDesign | undefined {
  if ((config.commonHeaderMode ?? "uniform") !== "uniform") return undefined;
  const instructions = config.commonHeaderDesign?.instructions?.trim();
  const references = config.svgDesignReferenceSnapshotId
    ? [config.svgDesignReferenceSnapshotId]
    : config.commonHeaderDesign?.reference_snapshot_ids;
  const documentValues = { ...config.commonHeaderDesign?.document_values };
  if (documentValues.document_label !== undefined && !documentValues.document_label.trim()) delete documentValues.document_label;
  const hasDocumentValues = Object.keys(documentValues).length > 0;
  const pageValues = config.commonHeaderDesign?.page_values;
  return instructions || references?.length || hasDocumentValues || pageValues?.length ? {
    ...(instructions ? { instructions } : {}),
    ...(references !== undefined ? { reference_snapshot_ids: references } : {}),
    ...(hasDocumentValues ? { document_values: documentValues } : {}),
    ...(pageValues !== undefined ? { page_values: pageValues } : {}),
  } : undefined;
}
