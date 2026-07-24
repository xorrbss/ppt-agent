"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { getApiUrl } from "@/utils/api";
import TemplateV2ImportReview from "./TemplateV2ImportReview";
import TemplateV2RepeatSuggestionReview, {
  type RepeatSuggestion,
} from "./TemplateV2RepeatSuggestionReview";

const IMPORT_ENDPOINT = "/api/v1/ppt/structured-templates/imports";
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;
const ACTIVE_STATES = new Set(["queued", "processing", "finalizing"]);

interface ImportRecord {
  id: string;
  requested_template_id: string;
  draft_template_id: string | null;
  state: string;
  revision: number;
  source_filename: string;
  source_size_bytes: number;
  pipeline_version: string;
  analysis_result: {
    provider?: {
      id?: string;
      status?: string;
      external_ai?: boolean;
    };
    preview?: { status?: string; reason?: string };
    render?: { status?: string; reason?: string };
    visual_fidelity?: {
      method?: string;
      status?: string;
      metrics?: {
        mean_absolute_error?: number;
        bad_pixel_ratio?: number;
        largest_bad_component?: number;
      };
      thresholds?: {
        mean_absolute_error?: number;
        bad_pixel_ratio?: number;
        largest_bad_component?: number;
      };
    } | null;
    summary?: {
      slide_count?: number;
      shape_count?: number;
      unsupported_shape_count?: number;
      visual_fidelity_status?: string;
      review_required?: boolean;
    };
  } | null;
  repeat_suggestions: RepeatSuggestion[];
  task_message: string | null;
}

function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join(
    ""
  );
}

async function responsePayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  return contentType.includes("application/json")
    ? response.json()
    : response.text();
}

function requestError(status: number, payload: unknown): string {
  if (
    payload &&
    typeof payload === "object" &&
    "detail" in payload &&
    typeof payload.detail === "string"
  ) {
    return payload.detail;
  }
  return `Request failed (${status})`;
}

async function readImportResponse(response: Response): Promise<ImportRecord> {
  const payload = await responsePayload(response);
  if (!response.ok) throw new Error(requestError(response.status, payload));
  return payload as ImportRecord;
}

export default function TemplateV2PptxImportPanel({
  currentTemplateId,
}: {
  currentTemplateId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [newTemplateId, setNewTemplateId] = useState(
    `${currentTemplateId.slice(0, 115)}-import`
  );
  const [file, setFile] = useState<File | null>(null);
  const [record, setRecord] = useState<ImportRecord | null>(null);
  const [acceptedRepeatIds, setAcceptedRepeatIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const uploadKey = useRef<{ identity: string; key: string } | null>(null);
  const activeImportId =
    record && ACTIVE_STATES.has(record.state) ? record.id : null;

  useEffect(() => {
    if (!activeImportId) return;
    let stopped = false;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const response = await fetch(
            getApiUrl(
              `${IMPORT_ENDPOINT}/${encodeURIComponent(activeImportId)}`
            )
          );
          const next = await readImportResponse(response);
          if (!stopped) setRecord(next);
        } catch (pollError) {
          if (!stopped) {
            setError(
              pollError instanceof Error
                ? pollError.message
                : "Unable to refresh import status"
            );
          }
        }
      })();
    }, 1200);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeImportId]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!file) {
      setError("Choose a .pptx file.");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".pptx")) {
      setError("Only .pptx files are accepted.");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError("The PPTX exceeds the 100 MB upload limit.");
      return;
    }
    const templateId = newTemplateId.trim();
    if (!templateId || templateId.length > 128) {
      setError("New template ID must be between 1 and 128 characters.");
      return;
    }

    const identity = [
      templateId,
      file.name,
      file.size,
      file.lastModified,
    ].join("\u0000");
    if (uploadKey.current?.identity !== identity) {
      uploadKey.current = { identity, key: newIdempotencyKey() };
    }

    const form = new FormData();
    form.set("template_id", templateId);
    form.set("pptx_file", file);
    setBusy(true);
    try {
      const response = await fetch(getApiUrl(IMPORT_ENDPOINT), {
        method: "POST",
        headers: { "Idempotency-Key": uploadKey.current.key },
        body: form,
      });
      setAcceptedRepeatIds([]);
      setRecord(await readImportResponse(response));
    } catch (uploadError) {
      setError(
        uploadError instanceof Error
          ? uploadError.message
          : "Unable to upload PPTX"
      );
    } finally {
      setBusy(false);
    }
  }

  async function mutate(action: "confirm" | "retry" | "cancel") {
    if (!record) return;
    if (
      action === "confirm" &&
      !window.confirm(
        "Create a new Template V2 from this reviewed deterministic candidate? Existing templates and presentations will not be converted."
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(
        getApiUrl(
          `${IMPORT_ENDPOINT}/${encodeURIComponent(record.id)}/${action}`
        ),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_revision: record.revision,
            ...(action === "confirm"
              ? { accepted_repeat_suggestion_ids: acceptedRepeatIds }
              : {}),
          }),
        }
      );
      setRecord(await readImportResponse(response));
    } catch (mutationError) {
      setError(
        mutationError instanceof Error
          ? mutationError.message
          : `Unable to ${action} import`
      );
    } finally {
      setBusy(false);
    }
  }

  const analysis = record?.analysis_result;
  const summary = analysis?.summary;

  return (
    <section className="border-b border-slate-800 bg-slate-900/70 px-6 py-3">
      <button
        type="button"
        className="text-sm font-semibold text-violet-200"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? "Hide" : "Open"} reviewed PPTX import
      </button>
      {!expanded ? null : (
        <div className="mt-3 grid gap-4 lg:grid-cols-[minmax(0,360px)_1fr]">
          <form className="space-y-3" onSubmit={upload}>
            <p className="text-xs text-slate-400">
              Analysis is local and deterministic. It does not run Vision,
              OCR, or an external AI provider.
            </p>
            <label className="block text-xs text-slate-300">
              New template ID
              <input
                value={newTemplateId}
                maxLength={128}
                onChange={(event) => setNewTemplateId(event.target.value)}
                className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
              />
            </label>
            <label className="block text-xs text-slate-300">
              PPTX source
              <input
                type="file"
                accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setError(null);
                }}
                className="mt-1 block w-full text-sm"
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="rounded bg-violet-500 px-4 py-2 text-sm font-semibold disabled:opacity-40"
            >
              {busy ? "Working…" : "Upload and analyze"}
            </button>
          </form>

          <div className="min-w-0 rounded-lg border border-slate-800 bg-slate-950/70 p-4 text-sm">
            {error ? (
              <p role="alert" className="mb-3 text-red-300">
                {error}
              </p>
            ) : null}
            {!record ? (
              <p className="text-slate-400">
                No import in this review session. Uploading never converts the
                current presentation.
              </p>
            ) : (
              <div className="space-y-3">
                <div>
                  <p className="font-semibold">
                    {record.source_filename} · {record.state}
                  </p>
                  <p className="text-xs text-slate-400">
                    {(record.source_size_bytes / 1024).toFixed(1)} KB ·{" "}
                    {record.pipeline_version} · revision {record.revision}
                  </p>
                  {record.task_message ? (
                    <p role="status" aria-live="polite" className="mt-1">
                      {record.task_message}
                    </p>
                  ) : null}
                </div>

                {analysis ? (
                  <>
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                      <dt className="text-slate-400">Analyzer</dt>
                      <dd>{analysis.provider?.id ?? "unknown"}</dd>
                      <dt className="text-slate-400">External AI</dt>
                      <dd>
                        {analysis.provider?.external_ai === false
                          ? "not used"
                          : "unknown"}
                      </dd>
                      <dt className="text-slate-400">Slides / shapes</dt>
                      <dd>
                        {summary?.slide_count ?? 0} /{" "}
                        {summary?.shape_count ?? 0}
                      </dd>
                      <dt className="text-slate-400">Unsupported shapes</dt>
                      <dd>{summary?.unsupported_shape_count ?? 0}</dd>
                      <dt className="text-slate-400">Preview / render</dt>
                      <dd>
                        {analysis.preview?.status ?? "unknown"} /{" "}
                        {analysis.render?.status ?? "unknown"}
                      </dd>
                      <dt className="text-slate-400">Visual fidelity</dt>
                      <dd>
                        {summary?.visual_fidelity_status ?? "not_evaluated"}
                      </dd>
                      {analysis.visual_fidelity ? (
                        <>
                          <dt className="text-slate-400">Pixel diff</dt>
                          <dd>
                            MAE{" "}
                            {analysis.visual_fidelity.metrics
                              ?.mean_absolute_error ?? "unknown"}
                            {" / "}bad pixels{" "}
                            {typeof analysis.visual_fidelity.metrics
                              ?.bad_pixel_ratio === "number"
                              ? `${(
                                  analysis.visual_fidelity.metrics
                                    .bad_pixel_ratio * 100
                                ).toFixed(2)}%`
                              : "unknown"}
                            {" / "}largest region{" "}
                            {analysis.visual_fidelity.metrics
                              ?.largest_bad_component ?? "unknown"}
                          </dd>
                        </>
                      ) : null}
                    </dl>
                    <TemplateV2ImportReview analysis={analysis} />
                  </>
                ) : null}

                <TemplateV2RepeatSuggestionReview
                  suggestions={record.repeat_suggestions}
                  state={record.state}
                  acceptedIds={acceptedRepeatIds}
                  onAcceptedIdsChange={setAcceptedRepeatIds}
                />

                <div className="flex flex-wrap gap-2">
                  {record.state === "review_required" ? (
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded bg-emerald-600 px-3 py-2 text-xs font-semibold disabled:opacity-40"
                      onClick={() => void mutate("confirm")}
                    >
                      Confirm and create new Template V2
                    </button>
                  ) : null}
                  {record.state === "failed" ? (
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded border border-slate-600 px-3 py-2 text-xs disabled:opacity-40"
                      onClick={() => void mutate("retry")}
                    >
                      Retry analysis
                    </button>
                  ) : null}
                  {[
                    "queued",
                    "processing",
                    "finalizing",
                    "failed",
                    "review_required",
                  ].includes(record.state) ? (
                    <button
                      type="button"
                      disabled={busy}
                      className="rounded border border-red-700 px-3 py-2 text-xs text-red-200 disabled:opacity-40"
                      onClick={() => void mutate("cancel")}
                    >
                      Cancel
                    </button>
                  ) : null}
                  {record.state === "confirmed" &&
                  record.draft_template_id ? (
                    <a
                      className="rounded bg-violet-500 px-3 py-2 text-xs font-semibold"
                      href={`/template-v2-studio/${encodeURIComponent(
                        record.draft_template_id
                      )}`}
                    >
                      Open confirmed template
                    </a>
                  ) : null}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
