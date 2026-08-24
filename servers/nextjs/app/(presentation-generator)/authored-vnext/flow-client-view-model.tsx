import type { InspectorModel, RegenerationCandidate as RegenerationCandidateView } from "@/components/authored-vnext";
import type { RegenerationCandidate, VisualDirection as FlowVisualDirection } from "@/lib/authored-vnext/flow";
import type { DeckMasterContractV1, VisualDirection as MasterVisualDirection } from "@/lib/authored-vnext/master/contracts";
import type { BodyNode, SlideDocument, WorkbenchState } from "@/lib/authored-vnext/workbench";

export function inspectorView(
  selectedNode: BodyNode | null,
  activeSlide: SlideDocument | null,
): InspectorModel {
  if (!selectedNode || !activeSlide) return { kind: "none" };
  const context = {
    slideId: activeSlide.id,
    elementId: selectedNode.id,
    expectedRevision: activeSlide.revision,
  };
  if (selectedNode.kind === "text" && selectedNode.editability.text) {
    return { kind: "text", context, text: selectedNode.text, style: selectedNode.style, styleEditable: selectedNode.editability.style };
  }
  if (selectedNode.kind === "shape" && selectedNode.editability.style) {
    return {
      kind: "shape",
      context,
      fill: selectedNode.fill,
      stroke: selectedNode.stroke,
      strokeWidth: selectedNode.strokeWidth,
      geometry: selectedNode.editability.geometry ? selectedNode.bounds : undefined,
    };
  }
  if (
    (selectedNode.kind === "image" || selectedNode.kind === "asset") &&
    selectedNode.editability.asset
  ) {
    return {
      kind: "asset",
      context,
      assetId: selectedNode.assetId,
      alt: selectedNode.altText,
      fit: selectedNode.fit,
      assetDigest: selectedNode.digest,
      mediaType: selectedNode.mediaType,
      storageReference: selectedNode.storageReference,
      sizeBytes: selectedNode.sizeBytes,
      cropGeometry: selectedNode.cropGeometry,
    };
  }
  if (
    (selectedNode.kind === "table" || selectedNode.kind === "chart") &&
    selectedNode.editability.data
  ) {
    return {
      kind: "data",
      context,
      nodeKind: selectedNode.kind,
      label: selectedNode.semanticRole,
      dataId: selectedNode.dataId,
      dataDigest: selectedNode.dataDigest,
      dataPayload: { ...(selectedNode.dataPayload ?? {}) },
      ...(selectedNode.kind === "table"
        ? { rows: selectedNode.rows, columns: selectedNode.columns }
        : {
            chartType: selectedNode.chartType,
            accessibilitySummary: selectedNode.accessibilitySummary,
          }),
      geometry: selectedNode.editability.geometry
        ? selectedNode.bounds
        : undefined,
    };
  }
  if (selectedNode.editability.geometry) {
    return { kind: "geometry", context, bounds: selectedNode.bounds };
  }
  // A server-declared false capability is an explicit read-only boundary. Do
  // not leave a raster island or an unadmitted native owner looking like an
  // empty inspector: users need the capability reason before they can choose
  // another owner to edit.
  return {
    kind: "capability",
    nodeKind: selectedNode.kind === "image" ? "asset" : selectedNode.kind,
    label: selectedNode.semanticRole,
    message: "이 객체는 현재 Scene revision에서 읽기 전용입니다. 지원되지 않는 표현은 raster island로 유지됩니다.",
    editability: selectedNode.editability,
  };
}

export function roleHintForLayout(
  layoutId: string,
): "cover" | "section" | "standard" | "longTitle" {
  if (layoutId === "layout:cover") return "cover";
  if (layoutId === "layout:section") return "section";
  if (layoutId === "layout:long-title") return "longTitle";
  return "standard";
}

export function restoredVisualDirection(
  direction: MasterVisualDirection,
): FlowVisualDirection {
  return {
    directionId: "direction:restored-deck",
    tone: direction.tone,
    imagery: direction.imagery ? [direction.imagery] : [],
    density: "balanced",
    notes: direction.notes,
  };
}

export function downloadArtifact(url: string): void {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "authored-vnext.pptx";
  anchor.rel = "noopener";
  anchor.hidden = true;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}

export function masterAssetDigests(
  contract: DeckMasterContractV1 | undefined,
): string[] {
  if (!contract) return [];
  return [...new Set(
    [contract.brandAssets.logo, contract.brandAssets.wordmark]
      .filter((asset) => asset !== null)
      .map((asset) => asset.digest),
  )];
}

export function regenerationCandidateView(
  candidate: RegenerationCandidate | null,
  activeSlide: SlideDocument | null,
  activeBodyText: string,
  label: string,
  summary: string,
): RegenerationCandidateView | null {
  if (!candidate || !activeSlide) return null;
  return {
    id: candidate.candidateDigest,
    slideId: candidate.slide.slide.slideId,
    basedOnRevision: activeSlide.revision,
    label,
    summary,
    before: <p>{activeBodyText}</p>,
    candidate: <p>{candidate.slide.slide.body}</p>,
  };
}

export function conflictView(
  conflict: WorkbenchState["conflict"],
  canonicalRevision: number | undefined,
  localRevision: number | undefined,
  message: string,
) {
  if (!conflict) return null;
  return {
    operationId: conflict.operationId,
    expectedRevision: conflict.expected.deckRevision,
    actualRevision:
      conflict.actual?.deckRevision ??
      canonicalRevision ??
      localRevision ??
      conflict.expected.deckRevision,
    message,
    canRebase: true,
  };
}
