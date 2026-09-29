"use client";

import React, { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  findDurableGenerationTask,
  subscribeToDurableGenerationTasks,
  type DurableGenerationTask,
} from "../hooks/durableGenerationTask";

interface ResumeGenerationBannerProps {
  presentationId: string | null;
  /** A run already in flight owns the overlay; never offer a second start. */
  suppressed?: boolean;
  onResume: () => void;
}

/**
 * An interrupted generation keeps its server-side slide checkpoints and its
 * compose-time settings in the durable task record, and a same-key re-submit
 * resumes from them, so this banner reuses the page's ordinary submit handler
 * as-is. Without it a reload strands the stored task behind a button that
 * gives the user no sign that the earlier progress is still recoverable.
 */
const ResumeGenerationBanner: React.FC<ResumeGenerationBannerProps> = ({
  presentationId,
  suppressed = false,
  onResume,
}) => {
  const t = useTranslations("generation");
  const [task, setTask] = useState<DurableGenerationTask | null>(null);

  useEffect(() => {
    const reconcile = () => {
      const stored = findDurableGenerationTask(presentationId);
      setTask(
        stored?.taskId && stored.status === "retryable-error" ? stored : null
      );
    };
    reconcile();
    // A successful resume rewrites the stored task, which is what retires this
    // banner; subscribing keeps that from needing a page-level re-render.
    return subscribeToDurableGenerationTasks(reconcile);
  }, [presentationId]);

  if (!task || suppressed) return null;

  // Only authored jobs resume from server-side checkpoints, so only they can
  // truthfully name the slide count that was already saved.
  const progress = task.mode === "authored" ? task.lastProgress : undefined;
  const message =
    progress &&
    Number.isFinite(progress.completed_slides) &&
    Number.isFinite(progress.total_slides) &&
    progress.total_slides > 0
      ? t("resumeBannerProgress", {
          completed: progress.completed_slides,
          total: progress.total_slides,
        })
      : t("resumeBannerDescription");

  return (
    <div
      data-cy="resume-generation-banner"
      className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-surface bg-[#F4F3FF] px-5 py-3"
    >
      <p className="text-sm font-medium text-[#2D2D2D]">{message}</p>
      <Button
        data-cy="resume-generation-submit"
        onClick={() => onResume()}
        className="h-9 rounded-full bg-[#7E3AF2] px-5 text-xs font-medium text-white shadow-none hover:bg-[#7E3AF2] hover:opacity-90"
      >
        {t("resumeBannerAction")}
      </Button>
    </div>
  );
};

export default ResumeGenerationBanner;
