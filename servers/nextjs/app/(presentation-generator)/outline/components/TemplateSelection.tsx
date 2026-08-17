"use client";

import React, { memo, useCallback } from "react";

import AuthoredStylePicker from "./AuthoredStylePicker";

/** New decks expose authored style presets only; layout engines are compatibility UI. */
const TemplateSelection: React.FC = memo(function TemplateSelection() {
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
          className="font-syne text-base font-bold text-gray-900"
        >
          AI 저작 스타일
        </h2>
        <p className="mt-1 break-words text-sm leading-6 text-gray-600">
          회사표준과 독립적으로 적용되며, 생성 시 하나의 구성으로 고정됩니다.
        </p>
      </div>
      <AuthoredStylePicker isActive onActivate={keepAuthoredActive} />
    </section>
  );
});

export default TemplateSelection;
