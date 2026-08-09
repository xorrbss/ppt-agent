"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import {
  applyTemplateV2SlideVariant,
  cancelTemplateV2SlideVariants,
  previewTemplateV2SlideVariants,
  restoreTemplateV2SlideVariant,
  type JsonRecord,
  type TemplateV2SlideVariantJournalEntry,
  type TemplateV2SlideVariantPreview,
  type TemplateV2VariantKind,
} from "@/lib/template-v2-slide-variants";

const ERROR_KEYS = {
  template_v2_variant_candidate_count_invalid:
    "slideVariants.errors.candidateCountInvalid",
  template_v2_variant_stale_revision:
    "slideVariants.errors.staleRevision",
  template_v2_variant_preview_stale: "slideVariants.errors.previewStale",
  template_v2_variant_restore_source_stale:
    "slideVariants.errors.restoreSourceStale",
} as const;

const VARIANT_KEYS = {
  data_focused: "slideVariants.kinds.data_focused",
  image_focused: "slideVariants.kinds.image_focused",
  executive_summary: "slideVariants.kinds.executive_summary",
} as const satisfies Record<TemplateV2VariantKind, string>;

export default function TemplateV2SlideVariantsPanel({
  layouts,
  layoutId,
  revision,
  disabled,
  onCommit,
}: {
  layouts: JsonRecord;
  layoutId: string;
  revision: number;
  disabled: boolean;
  onCommit(args: {
    layouts: JsonRecord;
    expectedDigest: string;
    historyKey: string;
    notice: string;
  }): void;
}) {
  const t = useTranslations("studio");
  const [preview, setPreview] =
    useState<TemplateV2SlideVariantPreview | null>(null);
  const [selectedKind, setSelectedKind] =
    useState<TemplateV2VariantKind | null>(null);
  const [journalEntry, setJournalEntry] =
    useState<TemplateV2SlideVariantJournalEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  function formatValue(value: unknown): string {
    const serialized = JSON.stringify(value);
    return serialized === undefined
      ? t("slideVariants.undefinedValue")
      : serialized;
  }

  function variantError(code: string): string {
    if (Object.prototype.hasOwnProperty.call(ERROR_KEYS, code)) {
      return t(ERROR_KEYS[code as keyof typeof ERROR_KEYS]);
    }
    return t("slideVariants.errors.unavailable");
  }

  function variantLabel(kind: TemplateV2VariantKind): string {
    return t(VARIANT_KEYS[kind]);
  }

  function generatePreview() {
    const result = previewTemplateV2SlideVariants({
      layouts,
      layoutId,
      sourceRevision: revision,
    });
    setStatus(null);
    setJournalEntry(null);
    if (!result.ok) {
      setPreview(null);
      setSelectedKind(null);
      setError(variantError(result.code));
      return;
    }
    setPreview(result.value);
    setSelectedKind(result.value.candidates[0]?.kind ?? null);
    setError(null);
  }

  function cancelPreview() {
    if (preview) cancelTemplateV2SlideVariants(preview);
    setPreview(null);
    setSelectedKind(null);
    setError(null);
    setStatus(t("slideVariants.previewCanceled"));
  }

  function applySelected() {
    if (!preview || !selectedKind) return;
    const result = applyTemplateV2SlideVariant({
      layouts,
      preview,
      selectedKind,
      expectedRevision: preview.sourceRevision,
      currentRevision: revision,
    });
    if (!result.ok) {
      setError(variantError(result.code));
      return;
    }
    onCommit({
      layouts: result.value.layouts,
      expectedDigest: result.value.sourceDigest,
      historyKey: `slide-variant:${preview.id}:${selectedKind}`,
      notice: t("slideVariants.appliedNotice"),
    });
    setJournalEntry(result.value.journalEntry);
    setPreview(null);
    setSelectedKind(null);
    setError(null);
    setStatus(t("slideVariants.appliedStatus"));
  }

  function restoreOriginal() {
    if (!journalEntry) return;
    const result = restoreTemplateV2SlideVariant({
      layouts,
      journalEntry,
      expectedRevision: revision,
      currentRevision: revision,
    });
    if (!result.ok) {
      setError(variantError(result.code));
      return;
    }
    onCommit({
      layouts: result.value.layouts,
      expectedDigest: result.value.sourceDigest,
      historyKey: `slide-variant-restore:${journalEntry.appliedDigest}`,
      notice: t("slideVariants.restoredNotice"),
    });
    setJournalEntry(null);
    setError(null);
    setStatus(t("slideVariants.restoredStatus"));
  }

  return (
    <section
      aria-label={t("slideVariants.ariaLabel")}
      className="mt-5 rounded-xl border border-cyan-500/30 bg-cyan-950/20 p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-cyan-100">
            {t("slideVariants.title")}
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            {t("slideVariants.description")}
          </p>
        </div>
        <span className="rounded bg-slate-950 px-2 py-1 text-[10px] text-emerald-300">
          {t("slideVariants.boundedCount")}
        </span>
      </div>

      <button
        type="button"
        className="mt-3 w-full rounded bg-cyan-600 px-3 py-2 text-xs font-semibold disabled:opacity-40"
        disabled={disabled}
        onClick={generatePreview}
      >
        {t("slideVariants.compare")}
      </button>

      {error ? (
        <p role="alert" className="mt-3 text-xs text-red-300">
          {error}
        </p>
      ) : null}
      {status ? (
        <p role="status" className="mt-3 text-xs text-emerald-300">
          {status}
        </p>
      ) : null}

      {preview ? (
        <div
          className="mt-3 space-y-2"
          aria-label={t("slideVariants.candidatesAria")}
        >
          {preview.candidates.map((candidate) => (
            <label
              key={candidate.kind}
              className={`block rounded-lg border p-2 ${
                selectedKind === candidate.kind
                  ? "border-cyan-400 bg-cyan-950/40"
                  : "border-slate-700 bg-slate-900"
              }`}
            >
              <span className="flex items-center gap-2 text-xs font-medium">
                <input
                  type="radio"
                  name={`slide-variant-${preview.id}`}
                  checked={selectedKind === candidate.kind}
                  onChange={() => setSelectedKind(candidate.kind)}
                />
                {variantLabel(candidate.kind)}
              </span>
              <span className="mt-1 block text-[10px] text-slate-500">
                {t("slideVariants.digests", {
                  semantic: candidate.semanticDigest.slice(0, 12),
                  render: candidate.renderDigest.slice(0, 12),
                })}
              </span>
              <ul className="mt-2 space-y-1 text-[11px] text-slate-300">
                {candidate.patches.map((patch) => (
                  <li key={JSON.stringify(patch.path)}>
                    {patch.path.join(".")}:{" "}
                    <del className="text-red-200">
                      {formatValue(patch.before)}
                    </del>{" "}
                    →{" "}
                    <ins className="text-emerald-200 no-underline">
                      {formatValue(patch.after)}
                    </ins>
                  </li>
                ))}
              </ul>
            </label>
          ))}
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              className="flex-1 rounded bg-emerald-600 px-3 py-2 text-xs font-semibold disabled:opacity-40"
              disabled={disabled || !selectedKind}
              onClick={applySelected}
            >
              {t("slideVariants.applySelected")}
            </button>
            <button
              type="button"
              className="rounded border border-slate-600 px-3 py-2 text-xs"
              onClick={cancelPreview}
            >
              {t("slideVariants.cancel")}
            </button>
          </div>
        </div>
      ) : null}

      {journalEntry ? (
        <button
          type="button"
          className="mt-3 w-full rounded border border-amber-500/50 px-3 py-2 text-xs text-amber-200 disabled:opacity-40"
          disabled={disabled}
          onClick={restoreOriginal}
        >
          {t("slideVariants.restoreOriginal")}
        </button>
      ) : null}
    </section>
  );
}
