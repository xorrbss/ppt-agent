"use client";
import React, { useEffect, useMemo, useCallback, memo, useState } from "react";
import { Check } from "lucide-react";

import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import { selectableTemplates } from "@/app/presentation-templates";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { CustomTemplates, useCustomTemplateSummaries } from "@/app/hooks/useCustomTemplates";
import AuthoredStylesApi, {
  DEFAULT_AUTHORED_STYLE,
} from "@/app/(presentation-generator)/services/api/authored";
import type { AuthoredStyleSummary } from "@/app/(presentation-generator)/services/api/authored";

import CreateCustomTemplate from "../../(dashboard)/templates/components/CreateCustomTemplate";
import { CustomTemplateCard } from "./CustomTemplateCard";
import {
  TemplatePreviewStage,
  InbuiltTemplatePreview,
} from "../../components/TemplatePreviewComponents";

const BuiltInTemplateCard = memo(function BuiltInTemplateCard({
  template,
  isSelected,
  onSelect,
}: {
  template: TemplateLayoutsWithSettings;
  isSelected: boolean;
  onSelect: (template: TemplateLayoutsWithSettings) => void;
}) {
  const handleClick = useCallback(() => onSelect(template), [onSelect, template]);

  return (
    <Card
      className={cn(
        "cursor-pointer relative hover:shadow-sm transition-all duration-200 group overflow-hidden rounded-[22px] bg-white border",
        isSelected
          ? " border-blue-500 ring-2 ring-blue-500/25 shadow-sm"
          : " border-[#E8E9EC]"
      )}
      onClick={handleClick}
    >
      <TemplatePreviewStage>
        <InbuiltTemplatePreview layouts={template.layouts} templateId={template.id} isOutline={true} />
      </TemplatePreviewStage>
      <div className="flex items-center justify-between px-6 py-5 bg-white border-t border-[#EDEEEF] relative z-40">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-bold text-gray-900 capitalize font-syne">
            {template.name}
          </h3>
          <p className="text-xs text-gray-600 line-clamp-2 font-syne">
            {template.description}
          </p>
        </div>
      </div>
    </Card>
  );
});

// Sentinel id for the authored (high-quality) mode — not a real layout template.
export const AUTHORED_TEMPLATE_ID = "authored";

function handleAuthoredRadioKeyDown(
  event: React.KeyboardEvent<HTMLButtonElement>
) {
  const direction =
    event.key === "ArrowRight" || event.key === "ArrowDown"
      ? 1
      : event.key === "ArrowLeft" || event.key === "ArrowUp"
        ? -1
        : 0;
  const isBoundaryKey = event.key === "Home" || event.key === "End";
  if (direction === 0 && !isBoundaryKey) return;

  const group = event.currentTarget.closest('[role="radiogroup"]');
  const radios = Array.from(
    group?.querySelectorAll<HTMLButtonElement>('button[role="radio"]') ?? []
  );
  const currentIndex = radios.indexOf(event.currentTarget);
  if (currentIndex < 0 || radios.length === 0) return;

  event.preventDefault();
  const nextIndex =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? radios.length - 1
        : (currentIndex + direction + radios.length) % radios.length;
  radios[nextIndex].focus();
  radios[nextIndex].click();
}

const AuthoredStyleCard = memo(function AuthoredStyleCard({
  style,
  isSelected,
  isTabStop,
  onSelect,
  visionQa,
  onToggleVisionQa,
}: {
  style: AuthoredStyleSummary;
  isSelected: boolean;
  isTabStop: boolean;
  onSelect: (styleId: string) => void;
  visionQa: boolean;
  onToggleVisionQa: (next: boolean) => void;
}) {
  return (
    <Card
      data-testid={`authored-style-card-${style.id}`}
      className={cn(
        "relative flex min-w-0 flex-col overflow-hidden rounded-[22px] border bg-white transition-all duration-200 hover:shadow-sm",
        isSelected
          ? " border-blue-500 ring-2 ring-blue-500/25 shadow-sm"
          : " border-[#E8E9EC]"
      )}
    >
      <button
        type="button"
        role="radio"
        aria-checked={isSelected}
        aria-label={`${style.name} AI 저작 스타일 선택`}
        tabIndex={isTabStop ? 0 : -1}
        data-testid={`authored-style-select-${style.id}`}
        className="min-w-0 cursor-pointer text-left outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-inset"
        onClick={() => onSelect(style.id)}
        onKeyDown={handleAuthoredRadioKeyDown}
      >
        <div
          data-testid={`authored-style-preview-${style.id}`}
          className="relative flex h-[150px] items-center justify-center overflow-hidden px-5"
          style={{ backgroundColor: style.preview.bg }}
          aria-hidden="true"
        >
          <div
            className="absolute inset-x-0 bottom-0 h-3"
            style={{ backgroundColor: style.preview.accent }}
          />
          <div className="w-full max-w-[220px] rounded-xl border border-black/10 bg-white/95 p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[10px] font-semibold tracking-[0.14em] text-slate-600">
                AI AUTHORED
              </span>
              <span
                className="h-4 w-10 rounded-full"
                style={{ backgroundColor: style.preview.accent }}
              />
            </div>
            <div className="mt-4 h-2 w-3/4 rounded-full bg-slate-800/80" />
            <div className="mt-2 h-1.5 w-full rounded-full bg-slate-400/45" />
            <div className="mt-1.5 h-1.5 w-2/3 rounded-full bg-slate-400/35" />
          </div>
        </div>
        <div className="min-w-0 border-t border-[#EDEEEF] bg-white px-5 py-4">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <h3 className="min-w-0 break-words text-sm font-bold text-gray-900 font-syne">
              {style.name}
            </h3>
            {isSelected && (
              <span className="flex shrink-0 items-center gap-1 rounded-full bg-blue-50 px-2 py-1 text-[11px] font-semibold text-blue-700">
                <Check className="h-3 w-3" aria-hidden="true" />
                선택됨
              </span>
            )}
          </div>
          <p className="mt-1 break-words text-xs leading-5 text-gray-600 font-syne">
            {style.description}
          </p>
        </div>
      </button>
      {isSelected && (
        <div className="border-t border-[#EDEEEF] bg-blue-50/40 px-5 py-3">
          <label className="flex cursor-pointer select-none items-start gap-2 text-xs text-gray-700">
            <input
              type="checkbox"
              checked={visionQa}
              onChange={(e) => onToggleVisionQa(e.target.checked)}
              data-testid="authored-vision-qa"
              className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-blue-600"
            />
            <span className="leading-5">
              <span className="font-semibold">고품질 검수</span>
              <span className="block text-gray-500">vision-QA · 더 느림</span>
            </span>
          </label>
        </div>
      )}
    </Card>
  );
});

interface TemplateSelectionProps {
  selectedTemplate: (TemplateLayoutsWithSettings | string) | null;
  onSelectTemplate: (template: TemplateLayoutsWithSettings | string) => void;
}

const TemplateSelection: React.FC<TemplateSelectionProps> = memo(function TemplateSelection({
  selectedTemplate,
  onSelectTemplate,
}) {
  useEffect(() => {
    const existingScript = document.querySelector(
      'script[src*="tailwindcss.com"]'
    );
    if (!existingScript) {
      const script = document.createElement("script");
      script.src = "https://cdn.tailwindcss.com";
      script.async = true;
      document.head.appendChild(script);
    }
  }, []);

  const { templates: customTemplates, loading: customLoading } = useCustomTemplateSummaries();
  const dispatch = useDispatch();
  const [authoredStyles, setAuthoredStyles] = useState<AuthoredStyleSummary[]>([
    DEFAULT_AUTHORED_STYLE,
  ]);
  const [authoredStylesStatus, setAuthoredStylesStatus] = useState<
    "loading" | "ready" | "fallback"
  >("loading");
  const authoredVisionQa = useSelector(
    (s: RootState) => s.pptGenUpload.authoredVisionQa
  );
  const authoredStyle = useSelector(
    (s: RootState) => s.pptGenUpload.authoredStyle
  );

  useEffect(() => {
    let cancelled = false;

    AuthoredStylesApi.getStyles()
      .then((styles) => {
        if (cancelled) return;
        setAuthoredStyles(styles);
        setAuthoredStylesStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setAuthoredStyles([DEFAULT_AUTHORED_STYLE]);
        setAuthoredStylesStatus("fallback");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Stable identity so memo(AuthoredStyleCard) isn't defeated on every parent render.
  const handleToggleVisionQa = useCallback(
    (next: boolean) => dispatch(setPptGenUploadState({ authoredVisionQa: next })),
    [dispatch]
  );

  const handleAuthoredStyleSelect = useCallback(
    (styleId: string) => {
      dispatch(setPptGenUploadState({ authoredStyle: styleId }));
      onSelectTemplate(AUTHORED_TEMPLATE_ID);
    },
    [dispatch, onSelectTemplate]
  );

  const handleCustomSelect = useCallback(
    (template: TemplateLayoutsWithSettings | string) => onSelectTemplate(template),
    [onSelectTemplate]
  );

  const handleBuiltInSelect = useCallback(
    (template: TemplateLayoutsWithSettings) => onSelectTemplate(template),
    [onSelectTemplate]
  );

  const selectedCustomId = useMemo(
    () => (typeof selectedTemplate === "string" ? selectedTemplate : null),
    [selectedTemplate]
  );

  const selectedBuiltInId = useMemo(
    () => (typeof selectedTemplate !== "string" ? selectedTemplate?.id ?? null : null),
    [selectedTemplate]
  );

  const selectedAuthoredStyleId = useMemo(
    () =>
      authoredStyles.some((style) => style.id === authoredStyle)
        ? authoredStyle
        : DEFAULT_AUTHORED_STYLE.id,
    [authoredStyle, authoredStyles]
  );

  const authoredStyleCards = useMemo(
    () =>
      authoredStyles.map((style) => (
        <AuthoredStyleCard
          key={style.id}
          style={style}
          isSelected={
            selectedTemplate === AUTHORED_TEMPLATE_ID &&
            selectedAuthoredStyleId === style.id
          }
          isTabStop={selectedAuthoredStyleId === style.id}
          onSelect={handleAuthoredStyleSelect}
          visionQa={authoredVisionQa}
          onToggleVisionQa={handleToggleVisionQa}
        />
      )),
    [
      authoredStyles,
      authoredVisionQa,
      handleAuthoredStyleSelect,
      handleToggleVisionQa,
      selectedAuthoredStyleId,
      selectedTemplate,
    ]
  );

  // Custom template cards as a flat list so they can flow inline in the unified
  // grid (after the "create" card, before the built-in templates). Empty while
  // loading — built-in templates and the create card render immediately.
  const customTemplateCards = useMemo(
    () =>
      customLoading
        ? []
        : customTemplates.map((template: CustomTemplates) => (
            <CustomTemplateCard
              key={template.id}
              template={template}
              onSelectTemplate={handleCustomSelect}
              selectedTemplate={selectedCustomId}
            />
          )),
    [customLoading, customTemplates, handleCustomSelect, selectedCustomId]
  );

  const builtInTemplateCards = useMemo(
    () =>
      selectableTemplates.map((template: TemplateLayoutsWithSettings) => (
        <BuiltInTemplateCard
          key={template.id}
          template={template}
          isSelected={selectedBuiltInId === template.id}
          onSelect={handleBuiltInSelect}
        />
      )),
    [selectedBuiltInId, handleBuiltInSelect]
  );

  return (
    <div className="mb-4 space-y-8">
      <section
        aria-labelledby="authored-template-heading"
        data-testid="authored-template-section"
      >
        <div className="mb-4">
          <h2
            id="authored-template-heading"
            className="text-base font-bold text-gray-900 font-syne"
          >
            AI 저작 템플릿(고품질)
          </h2>
          <p className="mt-1 break-words text-sm leading-6 text-gray-600">
            AI가 슬라이드별 디자인을 직접 저작하며, 인앱에서는 보기 전용으로 제공됩니다.
          </p>
        </div>
        <div
          role="radiogroup"
          aria-label="AI 저작 스타일"
          data-testid="authored-style-grid"
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
        >
          {authoredStyleCards}
        </div>
        <p
          role="status"
          aria-live="polite"
          className="mt-2 min-h-5 text-xs text-gray-500"
        >
          {authoredStylesStatus === "loading" &&
            "스타일 목록을 불러오는 중입니다. 기본 스타일은 바로 선택할 수 있습니다."}
          {authoredStylesStatus === "fallback" &&
            "스타일 목록을 불러오지 못해 기본 스타일을 표시합니다."}
        </p>
      </section>

      <section
        aria-labelledby="layout-template-heading"
        data-testid="layout-template-section"
      >
        <div className="mb-4">
          <h2
            id="layout-template-heading"
            className="text-base font-bold text-gray-900 font-syne"
          >
            레이아웃 템플릿(편집 가능 PPTX)
          </h2>
          <p className="mt-1 break-words text-sm leading-6 text-gray-600">
            기존 레이아웃을 사용해 PowerPoint에서 편집 가능한 슬라이드를 만듭니다.
          </p>
        </div>
        <div
          data-testid="layout-template-grid"
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
        >
          <CreateCustomTemplate />
          {customTemplateCards}
          {builtInTemplateCards}
        </div>
      </section>
    </div>
  );
});

export default TemplateSelection;
