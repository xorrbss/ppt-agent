"use client";

import { useTranslations } from "next-intl";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import {
  editTemplateV2VectorPointTopology,
  templateV2ProvenVectorEditCapability,
  templateV2ProvenVectorWrapEditCapability,
  templateV2ProvenVectorWrapRemovalCapability,
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
  const t = useTranslations("studio");
  const firstPointCapability =
    templateV2ProvenVectorEditCapability(element);
  const wrapCapability =
    templateV2ProvenVectorWrapEditCapability(element);
  const wrapRemovalCapability =
    templateV2ProvenVectorWrapRemovalCapability(element);
  if (
    !firstPointCapability &&
    !wrapCapability &&
    !wrapRemovalCapability
  ) return null;

  return (
    <div className="flex items-center gap-1">
      {firstPointCapability && (
        <ToolbarButton
          className="px-1.5"
          aria-label={t("toolbar.vector.addPointAfter")}
          title={t("toolbar.vector.addPointAfterTitle")}
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
      )}
      {wrapCapability && (
        <ToolbarButton
          className="px-1.5"
          aria-label={t("toolbar.vector.addWrapPointAfterLast")}
          title={t("toolbar.vector.addWrapPointAfterLastTitle")}
          onClick={() => {
            const edit = editTemplateV2VectorPointTopology(element, {
              type: "add-after",
              index: 1,
            });
            if (!edit) return;
            onPatch(
              [{ path: ["points"], value: edit.points }],
              { historyKey: "toolbar:vector-point-add-after-last-wrap" }
            );
          }}
        >
          +↻
        </ToolbarButton>
      )}
      {wrapRemovalCapability && (
        <ToolbarButton
          className="px-1.5"
          aria-label={t("toolbar.vector.removeWrapMidpoint")}
          title={t("toolbar.vector.removeWrapMidpointTitle")}
          onClick={() => {
            const edit = editTemplateV2VectorPointTopology(element, {
              type: "remove-proven-wrap-midpoint",
              index: 2,
            });
            if (!edit) return;
            onPatch(
              [{ path: ["points"], value: edit.points }],
              { historyKey: "toolbar:vector-point-remove-proven-wrap" }
            );
          }}
        >
          −
        </ToolbarButton>
      )}
    </div>
  );
}
