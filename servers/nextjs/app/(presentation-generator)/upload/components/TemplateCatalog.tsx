"use client";

import React, { useCallback } from "react";

import AuthoredStylePicker from "../../outline/components/AuthoredStylePicker";

interface TemplateCatalogProps {
  authoredStyleId: string;
  onSelectAuthored: (styleId: string) => void;
  onActivateAuthored: (styleId: string) => void;
  selectedThemeId?: string | null;
  onSelectTheme?: (themeId: string) => void;
}

/**
 * Compatibility wrapper for older callers. New creation exposes authored
 * presets only; structured/built-in template engines are not generation
 * choices.
 */
const TemplateCatalog: React.FC<TemplateCatalogProps> = ({
  authoredStyleId,
  onSelectAuthored,
  onActivateAuthored,
}) => {
  const activate = useCallback(() => {
    onSelectAuthored(authoredStyleId);
    onActivateAuthored(authoredStyleId);
  }, [authoredStyleId, onActivateAuthored, onSelectAuthored]);

  return (
    <section
      aria-label="AI 저작 스타일"
      className="mx-auto mt-10 max-w-[1180px]"
      data-testid="authored-style-catalog"
    >
      <AuthoredStylePicker isActive onActivate={activate} />
    </section>
  );
};

export default TemplateCatalog;
