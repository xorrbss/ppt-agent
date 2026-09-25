"use client";

import React, { useEffect, useState } from "react";
import { ArrowRight, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useSelector } from "react-redux";

import AuthoredStylesApi, {
  DEFAULT_AUTHORED_STYLE,
  type AuthoredStyleSummary,
} from "../../services/api/authored";
import { AuthoredStylePreview } from "../../outline/components/AuthoredStyleCard";
import { Button } from "@/components/ui/button";
import type { RootState } from "@/store/store";

interface SelectedStyleSummaryProps {
  expanded: boolean;
  onChange: () => void;
}

export default function SelectedStyleSummary({
  expanded,
  onChange,
}: SelectedStyleSummaryProps) {
  const t = useTranslations("upload");
  const selectedStyleId = useSelector(
    (state: RootState) => state.pptGenUpload.authoredStyle,
  );
  const [styles, setStyles] = useState<AuthoredStyleSummary[]>([
    DEFAULT_AUTHORED_STYLE,
  ]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    AuthoredStylesApi.getStyles()
      .then((nextStyles) => {
        if (cancelled) return;
        setStyles(nextStyles);
      })
      .catch(() => {
        // The picker owns canonical validation and keeps its safe default.
        // This summary can still render that same fallback when the request
        // is unavailable.
        if (!cancelled) setStyles([DEFAULT_AUTHORED_STYLE]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const selectedStyle =
    styles.find((style) => style.id === selectedStyleId) ??
    DEFAULT_AUTHORED_STYLE;

  return (
    <section
      aria-labelledby="selected-style-summary-title"
      className="mb-6 rounded-2xl border border-slate-200/90 bg-white p-4 shadow-[0_8px_24px_-18px_rgb(15_23_42_/_25%)] sm:p-5"
      data-testid="selected-style-summary"
      aria-busy={loading}
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-4">
          <div className="w-28 shrink-0 overflow-hidden rounded-xl border border-slate-200 bg-slate-50 sm:w-36">
            <AuthoredStylePreview style={selectedStyle} compact />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">
              {t("simplifyStyleLabel")}
            </p>
            <h2
              id="selected-style-summary-title"
              className="mt-1 truncate text-base font-semibold text-slate-950"
            >
              {loading ? t("simplifyStyleLoading") : selectedStyle.name}
            </h2>
            <p className="mt-1 line-clamp-2 text-sm leading-5 text-slate-500">
              {selectedStyle.description}
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={onChange}
          aria-expanded={expanded}
          aria-controls="authored-style-catalog"
          className="shrink-0 border-slate-200 bg-white text-slate-700 hover:border-violet-200 hover:bg-violet-50 hover:text-violet-800"
          data-testid="change-authored-style"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : null}
          {expanded ? t("simplifyStyleDone") : t("simplifyStyleChange")}
          {!expanded && !loading ? (
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          ) : null}
        </Button>
      </div>
    </section>
  );
}
