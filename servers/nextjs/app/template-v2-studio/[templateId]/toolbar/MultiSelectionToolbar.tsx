"use client";

import { useTranslations } from "next-intl";

import SelectionActions from "./SelectionActions";
import type { TemplateV2ToolbarActions } from "./types";

export default function MultiSelectionToolbar({
  count,
  actions,
}: {
  count: number;
  actions: TemplateV2ToolbarActions;
}) {
  const t = useTranslations("studio");

  return (
    <>
      <span className="whitespace-nowrap text-xs font-medium text-violet-200">
        {t("toolbar.selection.selectedCount", { count })}
      </span>
      <SelectionActions actions={actions} multi />
    </>
  );
}
