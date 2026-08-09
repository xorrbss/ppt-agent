"use client";

import { useTranslations } from "next-intl";

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
  const t = useTranslations("studio");

  return (
    <div className="flex items-center gap-2">
      <ToolbarField label={t("toolbar.icon.color")}>
        <span aria-hidden="true">{t("toolbar.common.color")}</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label={t("toolbar.icon.color")}
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
        <ToolbarButton
          aria-label={t("toolbar.icon.change")}
          onClick={onChangeIcon}
        >
          {t("toolbar.icon.change")}
        </ToolbarButton>
      )}
    </div>
  );
}
