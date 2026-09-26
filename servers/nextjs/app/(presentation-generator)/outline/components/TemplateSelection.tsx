"use client";
import { useTranslations as useA2bTranslations } from "next-intl";

import React, { memo, useCallback } from "react";

import AuthoredStylePicker from "./AuthoredStylePicker";

/** New decks expose authored style presets only; layout engines are compatibility UI. */
const TemplateSelection: React.FC = memo(function TemplateSelection() {
  const tA2b = useA2bTranslations("outlineExtras");
  const keepAuthoredActive = useCallback(() => undefined, []);

  return (
    <section
      aria-labelledby="authored-style-heading"
      className="mb-4"
      data-testid="authored-style-section"
    >
      <div className="mb-4">
        <h2
          id="authored-style-heading"
          className="text-base font-semibold text-[#27251E]"
        >
          {tA2b("aiAuthoredStyle")}
        </h2>
        <p className="mt-1 break-words text-sm leading-6 text-[#6B6B73]">
          {tA2b("appliedIndependentlyOfCompanyStandardsAndFixed")}
        </p>
      </div>
      <AuthoredStylePicker isActive onActivate={keepAuthoredActive} />
    </section>
  );
});

export default TemplateSelection;
