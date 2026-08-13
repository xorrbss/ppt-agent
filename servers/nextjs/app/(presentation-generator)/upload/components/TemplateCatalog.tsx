"use client";

import Image from "next/image";
import Link from "next/link";
import { useTranslations } from "next-intl";
import React, { useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, Palette, Plus, RotateCcw } from "lucide-react";

import AuthoredStylesApi, {
  DEFAULT_AUTHORED_STYLE,
  type AuthoredStyleSummary,
} from "@/app/(presentation-generator)/services/api/authored";
import {
  makeTemplateV2SelectionId,
  type StructuredTemplateSummary,
  useStructuredTemplateSummaries,
} from "@/app/hooks/useStructuredTemplates";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { resolveBackendAssetUrl } from "@/utils/api";

import { AuthoredStylePreview } from "../../outline/components/AuthoredStyleCard";
import { AUTHORED_TEMPLATE_ID } from "../../outline/components/TemplateSelection";
import ThemeGallery from "./ThemeGallery";

type CatalogTab = "explore" | "mine";
type CatalogKind = "all" | "structured" | "authored";
type CatalogSort = "recommended" | "name";
type CatalogItem =
  | { kind: "structured"; template: StructuredTemplateSummary }
  | { kind: "authored"; style: AuthoredStyleSummary };

interface TemplateCatalogProps {
  selectedTemplateId: string | null;
  selectedThemeId: string | null;
  authoredStyleId: string;
  onSelectStructured: (selectionId: string) => void;
  onSelectAuthored: (styleId: string) => void;
  onSelectTheme: (themeId: string | null) => void;
}

function getThumbnail(template: StructuredTemplateSummary): string | null {
  const thumbnail = template.assets?.thumbnail;
  return typeof thumbnail === "string" && thumbnail.trim()
    ? resolveBackendAssetUrl(thumbnail.trim())
    : null;
}

function isPersonalTemplate(template: StructuredTemplateSummary): boolean {
  const sourceText = [
    typeof template.source === "string" ? template.source : "",
    template.provenance?.source,
    template.provenance?.source_name,
    template.provenance?.path,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  const repository =
    (template.source && typeof template.source === "object"
      ? template.source.repository
      : null) ??
    template.provenance?.repository;
  const hasDeclaredSource = Boolean(
    repository ||
      (typeof template.source === "string" && template.source.trim()) ||
      template.provenance?.source ||
      template.provenance?.source_name ||
      template.provenance?.path
  );

  return (
    /(custom|user|upload|local)/.test(sourceText) ||
    (!template.is_default && !hasDeclaredSource)
  );
}

function StructuredCatalogCard({
  template,
  selected,
  onSelect,
}: {
  template: StructuredTemplateSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const t = useTranslations("upload.catalog");
  const thumbnail = getThumbnail(template);

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={t("useTemplate", { name: template.name })}
      data-testid={`catalog-structured-${template.id}`}
      onClick={onSelect}
      className={cn(
        "group relative min-w-0 overflow-hidden rounded-xl border bg-white text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5] focus-visible:ring-offset-2",
        selected
          ? "border-[#5141E5] ring-2 ring-[#5141E5]/20"
          : "border-[#E9EAED] hover:-translate-y-0.5 hover:border-[#C9C6E8] hover:shadow-[0_12px_28px_rgba(15,23,42,0.12)]"
      )}
    >
      <span className="relative block aspect-video overflow-hidden bg-slate-100">
        {thumbnail ? (
          <Image
            src={thumbnail}
            alt={t("previewAlt", { name: template.name })}
            fill
            unoptimized
            loading="lazy"
            sizes="(max-width: 640px) 50vw, (max-width: 1024px) 45vw, 320px"
            className="object-cover transition-transform duration-300 group-hover:scale-[1.02] group-focus-visible:scale-[1.02] motion-reduce:transition-none"
          />
        ) : (
          <span className="absolute inset-0 overflow-hidden bg-[linear-gradient(135deg,#18336C_0%,#24469A_65%,#7190D5_100%)]">
            <span className="absolute -right-[12%] -top-[30%] h-[95%] w-[58%] rounded-full bg-white/10" />
            <span className="absolute -bottom-[40%] right-[18%] h-[80%] w-[42%] rounded-full bg-white/20" />
            <span className="absolute bottom-[20%] left-[7%] right-[8%] line-clamp-2 text-[clamp(11px,1.25vw,20px)] font-bold leading-tight text-white">
              {template.name}
            </span>
          </span>
        )}

        <span className="absolute inset-x-0 bottom-3 flex translate-y-3 justify-center opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100 group-focus-visible:translate-y-0 group-focus-visible:opacity-100 motion-reduce:transition-none">
          <span className="rounded-full bg-[#22252A] px-4 py-2 text-xs font-semibold text-white shadow-[0_4px_12px_rgba(0,0,0,0.15)]">
            {t("apply")}
          </span>
        </span>

        {template.is_default ? (
          <span className="absolute left-2 top-2 rounded-full bg-white/90 px-2 py-1 text-[10px] font-bold text-[#344054] shadow-sm backdrop-blur">
            {t("defaultBadge")}
          </span>
        ) : null}
        {selected ? (
          <span className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full bg-[#5141E5] text-white shadow-sm">
            <Check className="h-4 w-4" aria-hidden="true" />
          </span>
        ) : null}
      </span>
      <span className="flex min-h-11 items-center justify-between gap-2 border-t border-[#F0F1F3] px-3 py-2.5">
        <span className="truncate text-xs font-semibold text-[#303238] sm:text-sm">
          {template.name}
        </span>
        <span className="shrink-0 text-[10px] font-medium text-[#90949B]">
          {t("revision", { revision: template.revision })}
        </span>
      </span>
    </button>
  );
}

function AuthoredCatalogCard({
  style,
  selected,
  onSelect,
}: {
  style: AuthoredStyleSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const t = useTranslations("upload.catalog");

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={t("useTemplate", { name: style.name })}
      data-testid={`catalog-authored-${style.id}`}
      onClick={onSelect}
      className={cn(
        "group relative min-w-0 overflow-hidden rounded-xl border bg-white text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5] focus-visible:ring-offset-2",
        selected
          ? "border-[#5141E5] ring-2 ring-[#5141E5]/20"
          : "border-[#E9EAED] hover:-translate-y-0.5 hover:border-[#C9C6E8] hover:shadow-[0_12px_28px_rgba(15,23,42,0.12)]"
      )}
    >
      <div className="relative aspect-video overflow-hidden">
        <div className="transition-transform duration-300 group-hover:scale-[1.02] group-focus-visible:scale-[1.02] motion-reduce:transition-none">
          <AuthoredStylePreview style={style} compact />
        </div>
        <span className="absolute inset-x-0 bottom-3 flex translate-y-3 justify-center opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100 group-focus-visible:translate-y-0 group-focus-visible:opacity-100 motion-reduce:transition-none">
          <span className="rounded-full bg-[#22252A] px-4 py-2 text-xs font-semibold text-white shadow-[0_4px_12px_rgba(0,0,0,0.15)]">
            {t("apply")}
          </span>
        </span>
        {selected ? (
          <span className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full bg-[#5141E5] text-white shadow-sm">
            <Check className="h-4 w-4" aria-hidden="true" />
          </span>
        ) : null}
      </div>
      <span className="flex min-h-11 items-center justify-between gap-2 border-t border-[#F0F1F3] px-3 py-2.5">
        <span className="truncate text-xs font-semibold text-[#303238] sm:text-sm">
          {style.name}
        </span>
        <span className="shrink-0 text-[10px] font-medium text-[#7A5AF8]">AI</span>
      </span>
    </button>
  );
}

export default function TemplateCatalog({
  selectedTemplateId,
  selectedThemeId,
  authoredStyleId,
  onSelectStructured,
  onSelectAuthored,
  onSelectTheme,
}: TemplateCatalogProps) {
  const t = useTranslations("upload.catalog");
  const [tab, setTab] = useState<CatalogTab>("explore");
  const [kind, setKind] = useState<CatalogKind>("all");
  const [sort, setSort] = useState<CatalogSort>("recommended");
  const [authoredStyles, setAuthoredStyles] = useState<AuthoredStyleSummary[]>([
    DEFAULT_AUTHORED_STYLE,
  ]);
  const [authoredLoading, setAuthoredLoading] = useState(true);
  const {
    templates: structuredTemplates,
    loading: structuredLoading,
    error: structuredError,
    refetch,
  } = useStructuredTemplateSummaries();

  const defaultTemplates = useMemo(
    () => structuredTemplates.filter((template) => template.is_default),
    [structuredTemplates]
  );
  const catalogConfigurationError =
    !structuredLoading &&
    !structuredError &&
    structuredTemplates.length > 0 &&
    defaultTemplates.length !== 1;

  useEffect(() => {
    if (selectedTemplateId !== null || defaultTemplates.length !== 1) return;
    const template = defaultTemplates[0];
    onSelectStructured(makeTemplateV2SelectionId(template.id, template.revision));
  }, [defaultTemplates, onSelectStructured, selectedTemplateId]);

  useEffect(() => {
    let cancelled = false;
    AuthoredStylesApi.getStyles()
      .then((nextStyles) => {
        if (!cancelled) setAuthoredStyles(nextStyles);
      })
      .catch(() => {
        if (!cancelled) setAuthoredStyles([DEFAULT_AUTHORED_STYLE]);
      })
      .finally(() => {
        if (!cancelled) setAuthoredLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const visibleStructured = useMemo(() => {
    if (kind === "authored") return [];
    const templates =
      tab === "mine"
        ? structuredTemplates.filter(isPersonalTemplate)
        : structuredTemplates;
    return sort === "name"
      ? [...templates].sort((a, b) => a.name.localeCompare(b.name, "ko"))
      : templates;
  }, [kind, sort, structuredTemplates, tab]);

  const visibleAuthored = useMemo(() => {
    if (tab === "mine" || kind === "structured") return [];
    return sort === "name"
      ? [...authoredStyles].sort((a, b) => a.name.localeCompare(b.name, "ko"))
      : authoredStyles;
  }, [authoredStyles, kind, sort, tab]);

  const catalogItems = useMemo<CatalogItem[]>(() => {
    const structured =
      structuredError || catalogConfigurationError ? [] : visibleStructured;
    const structuredItems = structured.map(
      (template): CatalogItem => ({ kind: "structured", template })
    );
    const authoredItems = visibleAuthored.map(
      (style): CatalogItem => ({ kind: "authored", style })
    );

    if (
      tab !== "explore" ||
      kind !== "all" ||
      sort !== "recommended" ||
      structuredItems.length === 0 ||
      authoredItems.length === 0
    ) {
      return sort === "name"
        ? [...structuredItems, ...authoredItems].sort((left, right) => {
            const leftName =
              left.kind === "structured" ? left.template.name : left.style.name;
            const rightName =
              right.kind === "structured"
                ? right.template.name
                : right.style.name;
            return leftName.localeCompare(rightName, "ko");
          })
        : [...structuredItems, ...authoredItems];
    }

    const defaultIndex = structuredItems.findIndex(
      (item) => item.kind === "structured" && item.template.is_default
    );
    const defaultItem =
      defaultIndex >= 0 ? structuredItems[defaultIndex] : structuredItems[0];
    const remainingStructured = structuredItems.filter(
      (_, index) => index !== (defaultIndex >= 0 ? defaultIndex : 0)
    );
    const interleaved: CatalogItem[] = [defaultItem];
    const pairCount = Math.max(
      authoredItems.length,
      remainingStructured.length
    );

    for (let index = 0; index < pairCount; index += 1) {
      if (authoredItems[index]) interleaved.push(authoredItems[index]);
      if (remainingStructured[index]) {
        interleaved.push(remainingStructured[index]);
      }
    }
    return interleaved;
  }, [
    catalogConfigurationError,
    kind,
    sort,
    structuredError,
    tab,
    visibleAuthored,
    visibleStructured,
  ]);

  const resultCount = catalogItems.length;
  const isLoading =
    resultCount === 0 &&
    (structuredLoading || (tab === "explore" && authoredLoading));

  return (
    <section
      className={cn(
        "ppt-template-catalog",
        "mt-16 border-t border-[#ECEDEF] pt-0 md:mt-20"
      )}
      aria-labelledby="template-catalog-heading"
      data-testid="template-catalog"
    >
      <h2 id="template-catalog-heading" className="sr-only">
        {t("heading")}
      </h2>

      <div
        className="flex min-h-14 items-end gap-6"
        role="tablist"
        aria-label={t("tabsLabel")}
      >
        {(["explore", "mine"] as const).map((tabId) => (
          <button
            key={tabId}
            type="button"
            role="tab"
            aria-selected={tab === tabId}
            onClick={() => setTab(tabId)}
            className={cn(
              "relative min-h-11 px-0 pb-3 pt-3 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5] focus-visible:ring-offset-2",
              tab === tabId
                ? "text-[#242629]"
                : "text-[#777B83] hover:text-[#303238]"
            )}
          >
            {t(tabId)}
            {tab === tabId ? (
              <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-[#242629]" />
            ) : null}
          </button>
        ))}
      </div>

      <div className="border-t border-[#ECEDEF] pt-5">
        <div className="flex flex-wrap items-center gap-3">
          <label className="relative">
            <span className="sr-only">{t("kindLabel")}</span>
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value as CatalogKind)}
              className="h-10 appearance-none rounded-full border border-[#E5E7EA] bg-white py-0 pl-4 pr-10 text-sm font-medium text-[#35373C] outline-none transition hover:border-[#C8CBD0] focus:border-[#5141E5] focus:ring-2 focus:ring-[#5141E5]/15"
            >
              <option value="all">{t("allKinds")}</option>
              <option value="structured">{t("structured")}</option>
              <option value="authored">{t("authored")}</option>
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#777B83]" />
          </label>

          <Dialog>
            <DialogTrigger asChild>
              <button
                type="button"
                className="inline-flex h-10 items-center gap-2 rounded-full border border-[#E5E7EA] bg-white px-4 text-sm font-medium text-[#35373C] transition hover:border-[#C8CBD0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5]/25"
                data-testid="theme-settings-button"
              >
                <Palette className="h-4 w-4" aria-hidden="true" />
                {selectedThemeId ? t("themeSelected") : t("theme")}
                <ChevronDown className="h-4 w-4 text-[#777B83]" aria-hidden="true" />
              </button>
            </DialogTrigger>
            <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto rounded-2xl border-[#E5E7EA] bg-white p-5 sm:p-6">
              <DialogHeader>
                <DialogTitle>{t("themeDialogTitle")}</DialogTitle>
                <DialogDescription>{t("themeDialogDescription")}</DialogDescription>
              </DialogHeader>
              <ThemeGallery
                selectedTheme={selectedThemeId}
                onSelectTheme={onSelectTheme}
              />
            </DialogContent>
          </Dialog>

          <label className="relative">
            <span className="sr-only">{t("sortLabel")}</span>
            <select
              value={sort}
              onChange={(event) => setSort(event.target.value as CatalogSort)}
              className="h-10 appearance-none rounded-full border border-[#E5E7EA] bg-white py-0 pl-4 pr-10 text-sm font-medium text-[#35373C] outline-none transition hover:border-[#C8CBD0] focus:border-[#5141E5] focus:ring-2 focus:ring-[#5141E5]/15"
            >
              <option value="recommended">{t("recommended")}</option>
              <option value="name">{t("nameSort")}</option>
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#777B83]" />
          </label>

          <p
            className="ml-auto text-xs text-[#858991]"
            role="status"
            aria-live="polite"
          >
            {t("resultCount", { count: resultCount })}
          </p>
        </div>

        {catalogConfigurationError ? (
          <div
            className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
            role="alert"
            data-testid="template-v2-catalog-configuration-error"
          >
            {t("configurationError")}
          </div>
        ) : null}

        {structuredError && tab === "explore" ? (
          <div
            className="mt-6 flex items-center justify-between gap-4 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900"
            role="alert"
          >
            <span>{t("loadError")}</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void refetch()}
            >
              <RotateCcw className="mr-2 h-4 w-4" />
              {t("retry")}
            </Button>
          </div>
        ) : null}

        {isLoading ? (
          <div
            className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-4"
            role="status"
            aria-label={t("loading")}
          >
            {Array.from({ length: 8 }, (_, index) => (
              <div
                key={index}
                className="aspect-video animate-pulse rounded-xl bg-[#F0F1F3]"
              />
            ))}
          </div>
        ) : (
          <div
            className="ppt-template-grid mt-5"
            data-testid="template-catalog-grid"
          >
            {tab === "mine" ? (
              <Link
                href="/custom-template"
                className="group flex aspect-video min-w-0 flex-col items-center justify-center rounded-xl border border-[#E5E7EA] bg-[#FAFAFB] text-center transition hover:border-[#BDB7E8] hover:bg-[#F7F5FF] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5] focus-visible:ring-offset-2"
                data-testid="add-template-card"
              >
                <span className="flex h-11 w-11 items-center justify-center rounded-full border border-[#DFE1E5] bg-white text-[#303238] shadow-sm transition group-hover:scale-105 motion-reduce:transition-none">
                  <Plus className="h-6 w-6" aria-hidden="true" />
                </span>
                <span className="mt-3 text-sm font-semibold text-[#303238]">
                  {t("addTemplate")}
                </span>
              </Link>
            ) : null}

            {catalogItems.map((item) => {
              if (item.kind === "structured") {
                const selectionId = makeTemplateV2SelectionId(
                  item.template.id,
                  item.template.revision
                );
                return (
                  <StructuredCatalogCard
                    key={`structured-${item.template.id}`}
                    template={item.template}
                    selected={selectedTemplateId === selectionId}
                    onSelect={() => onSelectStructured(selectionId)}
                  />
                );
              }

              return (
                <AuthoredCatalogCard
                  key={`authored-${item.style.id}`}
                  style={item.style}
                  selected={
                    selectedTemplateId === AUTHORED_TEMPLATE_ID &&
                    authoredStyleId === item.style.id
                  }
                  onSelect={() => onSelectAuthored(item.style.id)}
                />
              );
            })}
          </div>
        )}

        {!isLoading &&
        resultCount === 0 ? (
          <div className="mt-8 rounded-2xl border border-dashed border-[#D9DCE1] bg-[#FAFAFB] px-5 py-10 text-center">
            <p className="text-sm font-semibold text-[#34363B]">
              {t("emptyTitle")}
            </p>
            <p className="mt-1 text-xs text-[#777B83]">
              {t("emptyDescription")}
            </p>
            {tab === "mine" ? (
              <Link
                href="/custom-template"
                className="mt-4 inline-flex h-10 items-center rounded-full bg-[#242629] px-5 text-sm font-semibold text-white"
              >
                <Plus className="mr-2 h-4 w-4" />
                {t("addTemplate")}
              </Link>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
