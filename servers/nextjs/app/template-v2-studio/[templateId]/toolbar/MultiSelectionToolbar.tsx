"use client";

import SelectionActions from "./SelectionActions";
import type { TemplateV2ToolbarActions } from "./types";

export default function MultiSelectionToolbar({
  count,
  actions,
}: {
  count: number;
  actions: TemplateV2ToolbarActions;
}) {
  return (
    <>
      <span className="whitespace-nowrap text-xs font-medium text-violet-200">
        {count} selected
      </span>
      <SelectionActions actions={actions} multi />
    </>
  );
}
