import {
  AUTHORED_VNEXT_SLIDE_COUNT,
  type AuthoredVnextFlowState,
  type FlowEvent,
} from "@/lib/authored-vnext/flow/types";
import { outlineForPrompt } from "@/lib/authored-vnext/flow/workbench-adapters";
import type { VisualDirection } from "@/lib/authored-vnext/master/contracts";

interface OutlineLabels {
  coverPurpose: string;
  slidePurpose: (ordinal: number) => string;
}

export function confirmedDirectionEvents(
  direction: VisualDirection,
  prompt: string,
  labels: OutlineLabels,
  slideCount: AuthoredVnextFlowState["slideCount"] = AUTHORED_VNEXT_SLIDE_COUNT,
): readonly FlowEvent[] {
  return [
    {
      type: "visualDirection.confirmed",
      visualDirection: {
        directionId: "direction:current-deck",
        tone: direction.tone,
        imagery: direction.imagery ? [direction.imagery] : [],
        density: "balanced",
        notes: direction.notes,
      },
    },
    {
      type: "outline.succeeded",
      plan: outlineForPrompt(
        prompt,
        slideCount,
        labels.coverPurpose,
        labels.slidePurpose,
      ),
    },
  ];
}
