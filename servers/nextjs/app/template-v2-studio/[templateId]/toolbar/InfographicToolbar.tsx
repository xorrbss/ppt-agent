"use client";

import { useTranslations } from "next-intl";

import {
  buildTemplateV2InfographicValuePatches,
  validateTemplateV2InfographicContract,
} from "@/lib/template-v2-infographic-contract";
import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import { ToolbarField, toolbarInputClass } from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

export default function InfographicToolbar({
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
  let contract: ReturnType<typeof validateTemplateV2InfographicContract>;
  try {
    contract = validateTemplateV2InfographicContract(element);
  } catch {
    return null;
  }

  const { minimum, maximum, value, type } = contract.metrics;
  const sliderStep = (maximum - minimum) / 100;

  function patchValue(nextValue: number) {
    const result = buildTemplateV2InfographicValuePatches(element, nextValue);
    if (result.ok && result.value.length > 0) {
      onPatch(result.value, {
        historyKey: "toolbar:infographic-value",
        continuous: true,
      });
    }
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-slate-300">
        {type === "gauge"
          ? t("toolbar.infographic.gauge")
          : t("toolbar.infographic.progress")}
      </span>
      <ToolbarField label={t("toolbar.infographic.value")}>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label={t("toolbar.infographic.value")}
          type="number"
          min={minimum}
          max={maximum}
          step="any"
          value={value}
          onChange={(event) => patchValue(event.currentTarget.valueAsNumber)}
        />
      </ToolbarField>
      <ToolbarField label={t("toolbar.infographic.valueSlider")}>
        <input
          className="w-28 accent-violet-400"
          aria-label={t("toolbar.infographic.valueSlider")}
          type="range"
          min={minimum}
          max={maximum}
          step={sliderStep}
          value={value}
          onChange={(event) => patchValue(event.currentTarget.valueAsNumber)}
        />
      </ToolbarField>
    </div>
  );
}
