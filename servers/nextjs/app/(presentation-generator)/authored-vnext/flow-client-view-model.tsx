import type { InspectorModel, RegenerationCandidate as RegenerationCandidateView } from "@/components/authored-vnext";
import type { RegenerationCandidate, VisualDirection as FlowVisualDirection } from "@/lib/authored-vnext/flow";
import type { DeckMasterContractV1, VisualDirection as MasterVisualDirection } from "@/lib/authored-vnext/master/contracts";
import type { BodyNode, SlideDocument, WorkbenchState } from "@/lib/authored-vnext/workbench";

export function inspectorView(
  selectedNode: BodyNode | null,
  activeSlide: SlideDocument | null,
): InspectorModel {
  if (selectedNode?.kind !== "text" || !activeSlide) return { kind: "none" };
  return {
    kind: "text",
    context: {
      slideId: activeSlide.id,
      elementId: selectedNode.id,
      expectedRevision: activeSlide.revision,
    },
    text: selectedNode.text,
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
