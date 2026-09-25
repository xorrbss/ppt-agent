function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export interface DocumentCanvasCapabilities {
  a4: {
    enabled: boolean;
    stage?: "disabled" | "candidate" | "verified";
    reason: string | null;
    selection?: { routeId: string | null; enabled: boolean; reason: string | null };
  };
  sourceContentSvg: {
    enabled: boolean;
    reason: string | null;
    canvasSource: "source-manifest";
    inputFormats: readonly ["pptx"];
    productVerified: boolean;
  };
}

export interface DocumentCanvasSelection {
  generationStrategy: "authored" | "svg";
  authoredStyle: string;
  sourceContentMode: boolean;
  hasCompanyStandard: boolean;
}

export function normalizeDocumentCanvasCapabilities(
  payload: unknown,
): DocumentCanvasCapabilities {
  if (!isRecord(payload) || !isRecord(payload.a4)) {
    return unavailableDocumentCanvasCapabilities();
  }
  const sourceContentSvg = isRecord(payload.sourceContentSvg)
    && payload.sourceContentSvg.canvasSource === "source-manifest"
    && Array.isArray(payload.sourceContentSvg.inputFormats)
    && payload.sourceContentSvg.inputFormats.length === 1
    && payload.sourceContentSvg.inputFormats[0] === "pptx"
    ? {
        enabled: payload.sourceContentSvg.enabled === true,
        reason: typeof payload.sourceContentSvg.reason === "string" ? payload.sourceContentSvg.reason : null,
        canvasSource: "source-manifest" as const,
        inputFormats: ["pptx"] as const,
        productVerified: payload.sourceContentSvg.productVerified === true,
      }
    : unavailableDocumentCanvasCapabilities().sourceContentSvg;
  return {
    a4: {
      enabled: payload.a4.enabled === true,
      stage: payload.a4.stage === "candidate" || payload.a4.stage === "verified" ? payload.a4.stage : "disabled",
      reason:
        typeof payload.a4.reason === "string" && payload.a4.reason.trim()
          ? payload.a4.reason
          : null,
      selection: isRecord(payload.a4.selection)
        ? {
            routeId: typeof payload.a4.selection.routeId === "string" ? payload.a4.selection.routeId : null,
            enabled: payload.a4.selection.enabled === true,
            reason: typeof payload.a4.selection.reason === "string" ? payload.a4.selection.reason : null,
          }
        : undefined,
    },
    sourceContentSvg,
  };
}

export function unavailableDocumentCanvasCapabilities(): DocumentCanvasCapabilities {
  return {
    a4: { enabled: false, reason: "document_canvas_capability_unavailable" },
    sourceContentSvg: {
      enabled: false,
      reason: "source_content_svg_capability_unavailable",
      canvasSource: "source-manifest",
      inputFormats: ["pptx"],
      productVerified: false,
    },
  };
}
