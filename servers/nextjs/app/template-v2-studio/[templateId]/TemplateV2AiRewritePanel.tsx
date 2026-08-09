"use client";

import { useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import {
  createTemplateV2AiRewriteWorkflow,
  createTemplateV2FakeAiRewriteProvider,
  type JsonRecord,
  type TemplateV2AiRewriteAction,
  type TemplateV2AiRewriteCandidate,
  type TemplateV2AiRewritePreview,
  type TemplateV2AiRewriteProviderRequest,
  type TemplateV2TextSelectionPatch,
} from "@/lib/template-v2-ai-rewrite";

type ActionKind = TemplateV2AiRewriteAction["kind"];

const ACTION_KINDS: readonly ActionKind[] = [
  "shorten",
  "expand",
  "tone-report",
  "tone-proposal",
  "translate",
];

function textRuns(
  element: JsonRecord,
): Array<{ runIndex: number; text: string }> {
  if (element.type !== "text" || !Array.isArray(element.runs)) return [];
  return element.runs.flatMap((run, runIndex) =>
    typeof run === "object" &&
    run !== null &&
    !Array.isArray(run) &&
    typeof (run as JsonRecord).text === "string"
      ? [{ runIndex, text: String((run as JsonRecord).text) }]
      : [],
  );
}

function graphemes(text: string): string[] {
  if (typeof Intl.Segmenter !== "function") return Array.from(text);
  return [
    ...new Intl.Segmenter("und", { granularity: "grapheme" }).segment(text),
  ].map((part) => part.segment);
}

export function generateTemplateV2StudioRewriteCandidates(
  request: TemplateV2AiRewriteProviderRequest,
): readonly string[] {
  const source = request.selectedText;
  const compact = graphemes(source)
    .slice(0, Math.max(1, Math.ceil(graphemes(source).length * 0.6)))
    .join("")
    .trim();
  const locale =
    request.action.kind === "translate"
      ? request.action.targetLocale.toUpperCase()
      : "";
  const candidates: string[] =
    request.action.kind === "shorten"
      ? [compact, `${compact}…`, `Key: ${compact}`]
      : request.action.kind === "expand"
        ? [
            `${source} — with supporting context`,
            `${source}. The key evidence and expected impact are included.`,
            `${source}: rationale, evidence, and next step`,
          ]
        : request.action.kind === "tone-report"
          ? [
              `${source}: confirmed result`,
              `${source} was observed in the review.`,
              `Finding — ${source}`,
            ]
          : request.action.kind === "tone-proposal"
            ? [
                `We propose ${source}.`,
                `${source} is recommended as the next step.`,
                `Proposal — ${source}`,
              ]
            : [
                `[${locale}] ${source}`,
                `${locale}: ${source}`,
                `${source} (${locale} preview)`,
              ];

  const unique: string[] = [];
  for (let index = 0; unique.length < request.candidateCount; index += 1) {
    const base = candidates[index] ?? `${source} · option ${index + 1}`;
    const candidate =
      base && base !== source && !unique.includes(base)
        ? base
        : `${source} · option ${index + 1}`;
    if (candidate !== source && !unique.includes(candidate)) {
      unique.push(candidate);
    }
  }
  return unique;
}

function errorKey(code: string) {
  if (code === "template_v2_ai_rewrite_not_grapheme_boundary") {
    return "aiRewrite.errors.selectionBoundary";
  }
  if (code === "template_v2_ai_rewrite_stale_revision") {
    return "aiRewrite.errors.staleRevision";
  }
  if (code === "template_v2_ai_rewrite_source_changed") {
    return "aiRewrite.errors.sourceChanged";
  }
  if (code === "template_v2_ai_rewrite_candidate_overflow") {
    return "aiRewrite.errors.overflow";
  }
  return "aiRewrite.errors.unavailable";
}

function CandidateDiff({
  candidate,
}: {
  candidate: TemplateV2AiRewriteCandidate;
}) {
  return (
    <p className="mt-2 break-words rounded bg-slate-950 p-2 text-xs leading-5">
      <span>{candidate.diff.unchangedPrefix}</span>
      {candidate.diff.removed ? (
        <del className="bg-red-950/70 text-red-200">
          {candidate.diff.removed}
        </del>
      ) : null}
      {candidate.diff.inserted ? (
        <ins className="bg-emerald-950/70 text-emerald-200 no-underline">
          {candidate.diff.inserted}
        </ins>
      ) : null}
      <span>{candidate.diff.unchangedSuffix}</span>
    </p>
  );
}

export default function TemplateV2AiRewritePanel({
  element,
  targetId,
  revision,
  disabled,
  onApply,
}: {
  element: JsonRecord;
  targetId: string;
  revision: number;
  disabled: boolean;
  onApply(patch: TemplateV2TextSelectionPatch, historyKey: string): void;
}) {
  const t = useTranslations("studio");
  const runs = textRuns(element);
  const [runIndex, setRunIndex] = useState(0);
  const [selection, setSelection] = useState<{
    start: number;
    end: number;
  } | null>(null);
  const [actionKind, setActionKind] = useState<ActionKind>("shorten");
  const [targetLocale, setTargetLocale] = useState("en");
  const [preview, setPreview] = useState<TemplateV2AiRewritePreview | null>(
    null,
  );
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const requestCounter = useRef(0);
  const workflow = useMemo(
    () =>
      createTemplateV2AiRewriteWorkflow(
        createTemplateV2FakeAiRewriteProvider(
          generateTemplateV2StudioRewriteCandidates,
        ),
      ),
    [],
  );

  if (!runs.length) return null;
  const safeRunIndex = Math.min(runIndex, runs.length - 1);
  const selectedRun = runs[safeRunIndex];
  const runText = selectedRun.text;
  const selectedCandidate =
    preview?.candidates.find((candidate) => candidate.id === candidateId) ??
    null;

  function closePreview() {
    if (preview) {
      workflow.cancel({
        previewId: preview.id,
        idempotencyKey: preview.idempotencyKey,
      });
    }
    setPreview(null);
    setCandidateId(null);
  }

  function action(): TemplateV2AiRewriteAction {
    return actionKind === "translate"
      ? { kind: "translate", targetLocale }
      : { kind: actionKind };
  }

  function captureSelection(target: HTMLTextAreaElement) {
    const start = target.selectionStart;
    const end = target.selectionEnd;
    closePreview();
    setSelection(end > start ? { start, end } : null);
    setError(null);
    setStatus(null);
  }

  async function generatePreview() {
    if (!selection || selection.end <= selection.start) {
      setError(t("aiRewrite.errors.selectRange"));
      return;
    }
    closePreview();
    setBusy(true);
    setError(null);
    setStatus(null);
    requestCounter.current += 1;
    const idempotencyKey = `ai-rewrite-ui-${revision}-${requestCounter.current}`;
    const result = await workflow.preview({
      targetId,
      element,
      selection: { runIndex: selectedRun.runIndex, ...selection },
      action: action(),
      expectedRevision: revision,
      currentRevision: revision,
      idempotencyKey,
      candidateCount: 3,
    });
    setBusy(false);
    if (!result.ok) {
      setError(t(errorKey(result.code)));
      return;
    }
    setPreview(result.preview);
    setCandidateId(
      result.preview.candidates.find((candidate) => candidate.applyable)?.id ??
        result.preview.candidates[0]?.id ??
        null,
    );
  }

  function applyCandidate() {
    if (!preview || !selectedCandidate) return;
    const result = workflow.apply({
      previewId: preview.id,
      candidateId: selectedCandidate.id,
      element,
      expectedRevision: preview.expectedRevision,
      currentRevision: revision,
      idempotencyKey: preview.idempotencyKey,
    });
    if (!result.ok) {
      setError(t(errorKey(result.code)));
      return;
    }
    onApply(result.patch, result.historyKey);
    setPreview(null);
    setCandidateId(null);
    setSelection(null);
    setError(null);
    setStatus(t("aiRewrite.status.applied"));
  }

  return (
    <section
      aria-label={t("aiRewrite.ariaLabel")}
      className="mt-5 rounded-xl border border-violet-500/30 bg-violet-950/20 p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-violet-100">
            {t("aiRewrite.title")}
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            {t("aiRewrite.description")}
          </p>
        </div>
        <span className="rounded bg-slate-950 px-2 py-1 text-[10px] text-emerald-300">
          {t("aiRewrite.failClosed")}
        </span>
      </div>

      {runs.length > 1 ? (
        <label className="mt-3 block text-xs text-slate-300">
          {t("aiRewrite.textRunLabel")}
          <select
            aria-label={t("aiRewrite.textRunAria")}
            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2"
            value={safeRunIndex}
            disabled={disabled || busy}
            onChange={(event) => {
              closePreview();
              setRunIndex(Number(event.target.value));
              setSelection(null);
              setError(null);
              setStatus(null);
            }}
          >
            {runs.map((run, index) => (
              <option key={run.runIndex} value={index}>
                {t("aiRewrite.runOption", { number: run.runIndex + 1 })}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <label className="mt-3 block text-xs text-slate-300">
        {t("aiRewrite.sourceLabel")}
        <textarea
          readOnly
          aria-label={t("aiRewrite.sourceRunAria", {
            number: selectedRun.runIndex + 1,
          })}
          className="mt-1 min-h-20 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-violet-400"
          value={runText}
          disabled={disabled || busy}
          onSelect={(event) => captureSelection(event.currentTarget)}
          onMouseUp={(event) => captureSelection(event.currentTarget)}
          onKeyUp={(event) => captureSelection(event.currentTarget)}
        />
      </label>
      <p className="mt-1 text-[11px] text-slate-500" aria-live="polite">
        {selection
          ? t("aiRewrite.selectedRange", {
              start: selection.start,
              end: selection.end,
            })
          : t("aiRewrite.noRangeSelected")}
      </p>

      <fieldset className="mt-3" disabled={disabled || busy}>
        <legend className="text-xs text-slate-300">
          {t("aiRewrite.actionLegend")}
        </legend>
        <div className="mt-1 flex flex-wrap gap-1">
          {ACTION_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              aria-pressed={actionKind === kind}
              onClick={() => {
                closePreview();
                setActionKind(kind);
                setError(null);
                setStatus(null);
              }}
              className={`rounded border px-2 py-1 text-xs ${
                actionKind === kind
                  ? "border-violet-400 bg-violet-500/30 text-violet-100"
                  : "border-slate-700 text-slate-300"
              }`}
            >
              {t(
                `aiRewrite.actions.${
                  kind === "tone-report"
                    ? "toneReport"
                    : kind === "tone-proposal"
                      ? "toneProposal"
                      : kind
                }`,
              )}
            </button>
          ))}
        </div>
      </fieldset>

      {actionKind === "translate" ? (
        <label className="mt-3 block text-xs text-slate-300">
          {t("aiRewrite.targetLocaleLabel")}
          <input
            aria-label={t("aiRewrite.targetLocaleAria")}
            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={targetLocale}
            disabled={disabled || busy}
            onChange={(event) => {
              closePreview();
              setTargetLocale(event.target.value);
            }}
          />
        </label>
      ) : null}

      <button
        type="button"
        className="mt-3 w-full rounded bg-violet-500 px-3 py-2 text-xs font-semibold disabled:opacity-40"
        disabled={
          disabled ||
          busy ||
          !selection ||
          (actionKind === "translate" && !targetLocale.trim())
        }
        onClick={() => void generatePreview()}
      >
        {busy
          ? t("aiRewrite.generating")
          : t("aiRewrite.generateCandidates", { count: 3 })}
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
          className="mt-4 space-y-2"
          aria-label={t("aiRewrite.candidatesAria")}
        >
          <p className="text-xs font-medium text-slate-200">
            {t("aiRewrite.before", { text: preview.selectedText })}
          </p>
          {preview.candidates.map((candidate, index) => (
            <label
              key={candidate.id}
              className={`block rounded-lg border p-2 ${
                candidate.id === candidateId
                  ? "border-violet-400 bg-violet-950/40"
                  : "border-slate-700 bg-slate-900"
              }`}
            >
              <span className="flex items-center justify-between gap-2 text-xs">
                <span>
                  <input
                    type="radio"
                    name={`rewrite-${preview.id}`}
                    value={candidate.id}
                    checked={candidate.id === candidateId}
                    onChange={() => setCandidateId(candidate.id)}
                  />{" "}
                  {t("aiRewrite.candidate", { number: index + 1 })}
                </span>
                <span
                  className={
                    candidate.applyable ? "text-emerald-300" : "text-amber-300"
                  }
                >
                  {candidate.applyable
                    ? t("aiRewrite.fits", {
                        estimated:
                          candidate.preflight.estimatedLines ??
                          t("aiRewrite.unknown"),
                        available:
                          candidate.preflight.availableLines ??
                          t("aiRewrite.unknown"),
                      })
                    : t("aiRewrite.overflow")}
                </span>
              </span>
              <CandidateDiff candidate={candidate} />
            </label>
          ))}
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              className="flex-1 rounded bg-emerald-600 px-3 py-2 text-xs font-semibold disabled:opacity-40"
              disabled={disabled || !selectedCandidate?.applyable}
              onClick={applyCandidate}
            >
              {t("aiRewrite.applySelected")}
            </button>
            <button
              type="button"
              className="rounded border border-slate-600 px-3 py-2 text-xs"
              onClick={() => {
                closePreview();
                setError(null);
                setStatus(t("aiRewrite.status.canceled"));
              }}
            >
              {t("aiRewrite.cancel")}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
