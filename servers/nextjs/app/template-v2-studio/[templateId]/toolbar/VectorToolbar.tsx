import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import {
  editTemplateV2VectorPointTopology,
  templateV2ProvenVectorEditCapability,
} from "@/lib/template-v2-vector";
import { ToolbarButton } from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

export default function VectorToolbar({
  element,
  onPatch,
}: {
  element: TemplateV2ToolbarRecord;
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: { historyKey?: string; continuous?: boolean }
  ) => void;
}) {
  if (!templateV2ProvenVectorEditCapability(element)) return null;

  return (
    <ToolbarButton
      className="px-1.5"
      aria-label="Add vector point after"
      title="Add a point after the first point"
      onClick={() => {
        const edit = editTemplateV2VectorPointTopology(element, {
          type: "add-after",
          index: 0,
        });
        if (!edit) return;
        onPatch(
          [{ path: ["points"], value: edit.points }],
          { historyKey: "toolbar:vector-point-add-after" }
        );
      }}
    >
      +
    </ToolbarButton>
  );
}
