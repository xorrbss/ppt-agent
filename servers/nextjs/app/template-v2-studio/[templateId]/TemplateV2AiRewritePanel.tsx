"use client";

import { useMemo, useRef, useState } from "react";

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

const ACTION_LABELS: ReadonlyArray<{ kind: ActionKind; label: string }> = [
  { kind: "shorten", label: "Shorter" },
  { kind: "expand", label: "Longer" },
  { kind: "tone-report", label: "Report tone" },
  { kind: "tone-proposal", label: "Proposal tone" },
  { kind: "translate", label: "Translate" },
];

function textRuns(
  element: JsonRecord
): Array<{ runIndex: number; text: string }> {
  if (element.type !== "text" || !Array.isArray(element.runs)) return [];
  return element.runs.flatMap((run, runIndex) =>
    typeof run === "object" &&
    run !== null &&
    !Array.isArray(run) &&
    typeof (run as JsonRecord).text === "string"
      ? [{ runIndex, text: String((run as JsonRecord).text) }]
      : []
  );
}

function graphemes(text: string): string[] {
  if (typeof Intl.Segmenter !== "function") return Array.from(text);
  return [
    ...new Intl.Segmenter("und", { granularity: "grapheme" }).segment(text),
  ].map((part) => part.segment);
}

export function generateTemplateV2StudioRewriteCandidates(
  request: TemplateV2AiRewriteProviderRequest
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

function errorMessage(code: string): string {
  if (code === "template_v2_ai_rewrite_not_grapheme_boundary") {
    return "Adjust the selection so it does not split an emoji or combined character.";
  }
  if (code === "template_v2_ai_rewrite_stale_revision") {
    return "The template revision changed. Generate a fresh preview.";
  }
  if (code === "template_v2_ai_rewrite_source_changed") {
    return "The source run changed. Select the text again.";
  }
  if (code === "template_v2_ai_rewrite_candidate_overflow") {
    return "This candidate would overflow the text box and cannot be applied.";
  }
  return `Rewrite unavailable (${code}).`;
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
  const runs = textRuns(element);
  const [runIndex, setRunIndex] = useState(0);
  const [selection, setSelection] = useState<{ start: number; end: number } | null>(
    null
  );
  const [actionKind, setActionKind] = useState<ActionKind>("shorten");
  const [targetLocale, setTargetLocale] = useState("en");
  const [preview, setPreview] = useState<TemplateV2AiRewritePreview | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const requestCounter = useRef(0);
  const workflow = useMemo(
    () =>
      createTemplateV2AiRewriteWorkflow(
        createTemplateV2FakeAiRewriteProvider(
          generateTemplateV2StudioRewriteCandidates
        )
      ),
    []
  );

  if (!runs.length) return null;
  const safeRunIndex = Math.min(runIndex, runs.length - 1);
  const selectedRun = runs[safeRunIndex];
  const runText = selectedRun.text;
  const selectedCandidate =
    preview?.candidates.find((candidate) => candidate.id === candidateId) ?? null;

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
      setError("Select a bounded range in the source run first.");
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
      setError(errorMessage(result.code));
      return;
    }
    setPreview(result.preview);
    setCandidateId(
      result.preview.candidates.find((candidate) => candidate.applyable)?.id ??
        result.preview.candidates[0]?.id ??
        null
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
      setError(errorMessage(result.code));
      return;
    }
    onApply(result.patch, result.historyKey);
    setPreview(null);
    setCandidateId(null);
    setSelection(null);
    setError(null);
    setStatus("Candidate applied. Autosave and global undo are available.");
  }

  return (
    <section
      aria-label="AI rewrite selected text"
      className="mt-5 rounded-xl border border-violet-500/30 bg-violet-950/20 p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-violet-100">
            AI rewrite · selected run only
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            Local deterministic preview · no network or paid provider
          </p>
        </div>
        <span className="rounded bg-slate-950 px-2 py-1 text-[10px] text-emerald-300">
          fail-closed
        </span>
      </div>

      {runs.length > 1 ? (
        <label className="mt-3 block text-xs text-slate-300">
          Text run
          <select
            aria-label="AI rewrite text run"
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
                Run {run.runIndex + 1}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <label className="mt-3 block text-xs text-slate-300">
        Select text in source
        <textarea
          readOnly
          aria-label={`AI rewrite source run ${selectedRun.runIndex + 1}`}
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
          ? `Selected UTF-16 range ${selection.start}–${selection.end}`
          : "No range selected"}
      </p>

      <fieldset className="mt-3" disabled={disabled || busy}>
        <legend className="text-xs text-slate-300">Rewrite action</legend>
        <div className="mt-1 flex flex-wrap gap-1">
          {ACTION_LABELS.map((item) => (
            <button
              key={item.kind}
              type="button"
              aria-pressed={actionKind === item.kind}
              onClick={() => {
                closePreview();
                setActionKind(item.kind);
                setError(null);
                setStatus(null);
              }}
              className={`rounded border px-2 py-1 text-xs ${
                actionKind === item.kind
                  ? "border-violet-400 bg-violet-500/30 text-violet-100"
                  : "border-slate-700 text-slate-300"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </fieldset>

      {actionKind === "translate" ? (
        <label className="mt-3 block text-xs text-slate-300">
          Target locale
          <input
            aria-label="AI rewrite target locale"
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
        {busy ? "Generating local preview…" : "Generate 3 candidates"}
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
        <div className="mt-4 space-y-2" aria-label="AI rewrite candidates">
          <p className="text-xs font-medium text-slate-200">
            Before: “{preview.selectedText}”
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
                  Candidate {index + 1}
                </span>
                <span
                  className={
                    candidate.applyable ? "text-emerald-300" : "text-amber-300"
                  }
                >
                  {candidate.applyable
                    ? `Fits · ${candidate.preflight.estimatedLines}/${candidate.preflight.availableLines} lines`
                    : "Overflow · apply blocked"}
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
              Apply selected
            </button>
            <button
              type="button"
              className="rounded border border-slate-600 px-3 py-2 text-xs"
              onClick={() => {
                closePreview();
                setError(null);
                setStatus("Preview canceled. No template data changed.");
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
