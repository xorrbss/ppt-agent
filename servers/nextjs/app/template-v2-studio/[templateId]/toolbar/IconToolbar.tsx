"use client";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import {
  ToolbarButton,
  ToolbarField,
  toolbarInputClass,
} from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

export default function IconToolbar({
  element,
  onPatch,
  onChangeIcon,
}: {
  element: TemplateV2ToolbarRecord;
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: { historyKey?: string; continuous?: boolean }
  ) => void;
  onChangeIcon?: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <ToolbarField label="Icon color">
        <span aria-hidden="true">Color</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label="Icon color"
          type="text"
          value={typeof element.color === "string" ? element.color : ""}
          placeholder="#000000"
          onChange={(event) =>
            onPatch([{ path: ["color"], value: event.target.value }], {
              historyKey: "toolbar:icon-color",
              continuous: true,
            })
          }
        />
      </ToolbarField>
      {onChangeIcon && (
        <ToolbarButton aria-label="Change icon" onClick={onChangeIcon}>
          Change icon
        </ToolbarButton>
      )}
    </div>
  );
}
