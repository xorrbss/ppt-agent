"use client";

import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Loader2, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { ReactReduxContext } from "react-redux";

import { Button } from "@/components/ui/button";
import type { RootState } from "@/store/store";

import { previewAuthoredPageRemakeBatch } from "../../services/api/authored-batch-remake";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { PageRemakePreviewImage } from "../components/PageRemakePreviewImage";
import { presentationMasterPin } from "../utils/masterPresentation";
import {
  batchRemakeIsComplete,
  batchRemakeIsStalled,
  decodeBatchRemakePayload,
  readyPreviews,
  type BatchRemakePayload,
  type BatchRemakeSlide,
} from "./batchRemakeStatus";
import type { WorkspaceCommandDispatcher } from "./types";

const POLL_INTERVAL_MS = 2_000;
const PREVIEW_TIMEOUT_MS = 15 * 60 * 1_000;

type BatchRemakeError = "start" | "timeout" | "stalled";

function placeholderSlides(slideIndexes: number[]): BatchRemakeSlide[] {
  return slideIndexes.map((slideIndex) => ({
    slideIndex,
    status: "pending",
    previewTaskId: "",
    warning: false,
  }));
}

export function WorkspaceBatchRemakeDialog({
  presentationId,
  slideIndexes,
  disabled = false,
  onWorkspaceCommand,
  onDismiss,
}: {
  presentationId: string;
  slideIndexes: number[];
  disabled?: boolean;
  onWorkspaceCommand: WorkspaceCommandDispatcher;
  onDismiss: () => void;
}) {
  const t = useTranslations("batchRemake");
  const reduxContext = useContext(ReactReduxContext);
  const [batchTaskId, setBatchTaskId] = useState<string | null>(null);
  const [payload, setPayload] = useState<BatchRemakePayload | null>(null);
  const [error, setError] = useState<BatchRemakeError | null>(null);
  const [excluded, setExcluded] = useState<Set<number>>(() => new Set());
  const [attempt, setAttempt] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  // One polling loop serves the whole batch, so a single monotonic run id is
  // enough to stop it on unmount and on approval.
  const runIdRef = useRef(0);
  // The array prop is rebuilt on every parent render; the join keeps the effect
  // from restarting the batch each time.
  const slideKey = slideIndexes.join(",");

  const masterPin = useMemo(() => {
    if (!reduxContext) return null;
    const state = reduxContext.store.getState() as RootState;
    return presentationMasterPin(state.presentationGeneration.presentationData);
  }, [reduxContext]);

  useEffect(() => {
    const runId = ++runIdRef.current;
    const requested = slideKey.split(",").map(Number);

    const poll = async () => {
      try {
        const started = await previewAuthoredPageRemakeBatch(
          presentationId,
          requested,
        );
        if (runIdRef.current !== runId) return;
        const taskId = started?.id;
        if (!taskId) {
          setError("start");
          return;
        }
        setBatchTaskId(taskId);

        const deadline = Date.now() + PREVIEW_TIMEOUT_MS;
        // A cancelled fan-out freezes `completed` instead of erroring, so the
        // loop watches for progress rather than only for the deadline.
        let lastProgressAt = Date.now();
        let lastCompleted = -1;
        while (Date.now() < deadline) {
          await new Promise((resolve) =>
            window.setTimeout(resolve, POLL_INTERVAL_MS),
          );
          if (runIdRef.current !== runId) return;
          const task =
            await PresentationGenerationApi.getGenerationStatus(taskId);
          if (runIdRef.current !== runId) return;
          const decoded = decodeBatchRemakePayload(task?.data);
          if (decoded) setPayload(decoded);
          if (decoded && batchRemakeIsComplete(decoded)) return;
          if (decoded && decoded.completed > lastCompleted) {
            lastCompleted = decoded.completed;
            lastProgressAt = Date.now();
          }
          if (batchRemakeIsStalled(decoded, { lastProgressAt, now: Date.now() })) {
            // Whatever finished before the stall stays approvable.
            setError("stalled");
            return;
          }
          // A failed child never fails the parent, so an errored parent means
          // the fan-out itself broke and no candidate is coming.
          if (task?.status === "error") {
            setError("start");
            return;
          }
        }
        setError("timeout");
      } catch {
        if (runIdRef.current === runId) setError("start");
      }
    };

    void poll();
    return () => {
      runIdRef.current += 1;
    };
  }, [presentationId, slideKey, attempt]);

  const slides = payload?.slides.length
    ? payload.slides
    : placeholderSlides(slideIndexes);
  const complete = batchRemakeIsComplete(payload);
  const ready = readyPreviews(payload);
  const selected = ready.filter((entry) => !excluded.has(entry.slide_index));
  const allFailed = complete && ready.length === 0;
  const canApprove =
    !disabled && !submitting && !masterPin && selected.length > 0;

  const dismiss = () => {
    runIdRef.current += 1;
    onDismiss();
  };

  /** Re-run the fan-out for the pages that never finished. */
  const restart = () => {
    setError(null);
    setPayload(null);
    setAttempt((current) => current + 1);
  };

  const toggle = (slideIndex: number) => {
    setExcluded((current) => {
      const next = new Set(current);
      if (next.has(slideIndex)) next.delete(slideIndex);
      else next.add(slideIndex);
      return next;
    });
  };

  const approve = async () => {
    if (!canApprove || !batchTaskId) return;
    setSubmitting(true);
    try {
      await onWorkspaceCommand(t("commandText", { count: selected.length }), {
        commandType: "slide.regenerate.batch",
        context: { batch_task_id: batchTaskId, previews: selected },
      });
      dismiss();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <article
      className="rounded-2xl border border-indigo-200 bg-white p-4 shadow-sm"
      data-testid="batch-remake-dialog"
      aria-label={t("title")}
      aria-live="polite"
    >
      <h3 className="text-sm font-semibold text-slate-900">{t("title")}</h3>
      <p className="mt-1 text-xs leading-5 text-slate-500">
        {t("description", { count: slideIndexes.length })}
      </p>

      <p
        className="mt-3 flex items-center gap-2 text-xs font-medium text-slate-600"
        data-testid="batch-remake-progress"
      >
        {!complete && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        {complete
          ? t("ready", { count: ready.length })
          : t("generating", {
              completed: payload?.completed ?? 0,
              total: payload?.total ?? slideIndexes.length,
            })}
      </p>

      <ul className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {slides.map((slide) => (
          <li
            key={slide.slideIndex}
            className="rounded-xl border border-slate-200 p-3"
            data-testid={`batch-remake-slide-${slide.slideIndex + 1}`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-slate-900">
                {t("slideLabel", { slide: slide.slideIndex + 1 })}
              </span>
              {slide.status === "previewed" && (
                <label className="flex items-center gap-1.5 text-xs text-slate-600">
                  <input
                    type="checkbox"
                    checked={!excluded.has(slide.slideIndex)}
                    onChange={() => toggle(slide.slideIndex)}
                    disabled={disabled || submitting}
                    aria-label={t("includeSlide", {
                      slide: slide.slideIndex + 1,
                    })}
                    data-testid={`batch-remake-include-${slide.slideIndex + 1}`}
                  />
                  {t("statusPreviewed")}
                </label>
              )}
              {slide.status === "pending" && (
                <span className="flex items-center gap-1 text-xs text-slate-500">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("statusPending")}
                </span>
              )}
              {slide.status === "failed" && (
                <span className="flex items-center gap-1 text-xs font-medium text-rose-700">
                  <XCircle className="h-3.5 w-3.5" />
                  {t("statusFailed")}
                </span>
              )}
            </div>

            {slide.status === "previewed" && slide.candidateImage && (
              <div className="mt-2">
                <PageRemakePreviewImage
                  image={slide.candidateImage}
                  pageNumber={slide.slideIndex + 1}
                />
              </div>
            )}
            {slide.warning && (
              <p
                className="mt-2 flex items-center gap-1 text-xs font-medium text-amber-800"
                data-testid={`batch-remake-warning-${slide.slideIndex + 1}`}
              >
                <AlertTriangle className="h-3.5 w-3.5" />
                {t("warningBadge")}
              </p>
            )}
            {/* A skipped page still has to say why, or the batch looks lossy. */}
            {slide.status === "failed" && slide.reasonCode && (
              <p className="mt-2 text-xs leading-5 text-rose-700">
                {t("failureReason", { reason: slide.reasonCode })}
              </p>
            )}
          </li>
        ))}
      </ul>

      {allFailed && (
        <p
          className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-950"
          role="alert"
          data-testid="batch-remake-all-failed"
        >
          {t("allFailed")}
        </p>
      )}
      {error && (
        <div
          className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-950"
          role="alert"
          data-testid="batch-remake-error"
        >
          <p>
            {error === "stalled"
              ? t("stalled")
              : error === "timeout"
                ? t("timeout")
                : t("startFailed")}
          </p>
          {error === "stalled" && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={restart}
              disabled={submitting}
              data-testid="batch-remake-restart"
            >
              {t("restart")}
            </Button>
          )}
        </div>
      )}
      {masterPin && (
        <p
          className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950"
          role="status"
          data-testid="batch-remake-unsupported-master"
        >
          {t("unsupportedMaster")}
        </p>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          onClick={dismiss}
          disabled={submitting}
          data-testid="batch-remake-cancel"
        >
          {t("cancel")}
        </Button>
        <Button
          type="button"
          onClick={() => void approve()}
          disabled={!canApprove}
          data-testid="batch-remake-approve"
        >
          {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
          {t("approve", { count: selected.length })}
        </Button>
      </div>
    </article>
  );
}
