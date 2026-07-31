"use client";

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

const ERROR_MESSAGES: Record<string, string> = {
  template_v2_variant_candidate_count_invalid:
    "This slide does not expose at least two compatible visual controls. No variant was generated.",
  template_v2_variant_stale_revision:
    "The server revision changed. Generate a fresh slide preview.",
  template_v2_variant_preview_stale:
    "The slide changed after preview. Generate fresh candidates.",
  template_v2_variant_restore_source_stale:
    "Restore is blocked because the slide changed after the variant was applied.",
};

function formatValue(value: unknown): string {
  const serialized = JSON.stringify(value);
  return serialized === undefined ? "undefined" : serialized;
}

function variantError(code: string): string {
  return ERROR_MESSAGES[code] ?? `Variant unavailable (${code}).`;
}

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
  const [preview, setPreview] =
    useState<TemplateV2SlideVariantPreview | null>(null);
  const [selectedKind, setSelectedKind] =
    useState<TemplateV2VariantKind | null>(null);
  const [journalEntry, setJournalEntry] =
    useState<TemplateV2SlideVariantJournalEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

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
    setStatus("Preview canceled. No slide data changed.");
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
      notice: "Slide variant applied. Autosave and global undo are available.",
    });
    setJournalEntry(result.value.journalEntry);
    setPreview(null);
    setSelectedKind(null);
    setError(null);
    setStatus("Applied one slide-scoped visual patch.");
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
      notice: "Original slide restored from the variant journal.",
    });
    setJournalEntry(null);
    setError(null);
    setStatus("Restored the original slide-scoped snapshot.");
  }

  return (
    <section
      aria-label="Slide variant comparison"
      className="mt-5 rounded-xl border border-cyan-500/30 bg-cyan-950/20 p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-cyan-100">
            Slide variants
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            Deterministic, slide-scoped visual patches only
          </p>
        </div>
        <span className="rounded bg-slate-950 px-2 py-1 text-[10px] text-emerald-300">
          2–3 bounded
        </span>
      </div>

      <button
        type="button"
        className="mt-3 w-full rounded bg-cyan-600 px-3 py-2 text-xs font-semibold disabled:opacity-40"
        disabled={disabled}
        onClick={generatePreview}
      >
        Compare slide variants
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
        <div className="mt-3 space-y-2" aria-label="Slide variant candidates">
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
                {candidate.label}
              </span>
              <span className="mt-1 block text-[10px] text-slate-500">
                semantic {candidate.semanticDigest.slice(0, 12)} · render{" "}
                {candidate.renderDigest.slice(0, 12)}
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
              Apply selected
            </button>
            <button
              type="button"
              className="rounded border border-slate-600 px-3 py-2 text-xs"
              onClick={cancelPreview}
            >
              Cancel
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
          Restore original slide
        </button>
      ) : null}
    </section>
  );
}
