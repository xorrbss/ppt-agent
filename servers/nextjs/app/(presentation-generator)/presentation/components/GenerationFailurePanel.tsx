"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { refreshGenerationActivity } from "../../hooks/useGenerationActivity";
import { presentationHref } from "../../services/api/generation-activity-view";
import { postWorkspaceOperationAction } from "../../services/api/workspace-commands";
import {
  findResumedGeneration,
  generationFailureKey,
  isResumePrimary,
} from "../workspace/generationFailure";
import type { WorkspaceOperationActionOptions } from "../workspace/types";
import { WorkspacePartialRecovery } from "../workspace/WorkspacePartialRecovery";

const RESUME_POLL_MS = 2_000;
const RESUME_POLL_ATTEMPTS = 15;

/**
 * Failure guidance for a stopped generation: class-specific message, the
 * partial-recovery entry, and a fresh start. After a resume is accepted it
 * follows the new deck through the generation-activity list.
 */
export default function GenerationFailurePanel({ operationId, failureClass, fallbackText, resumable }: {
  operationId: string;
  failureClass: unknown;
  /** Shown instead of the class message when the server did not classify the failure. */
  fallbackText: string;
  /** False for states the recovery plan endpoint does not accept (e.g. expired). */
  resumable: boolean;
}) {
  const t = useTranslations("generationFailure");
  const router = useRouter();
  const [phase, setPhase] = useState<"idle" | "resuming" | "notFound">("idle");
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const followResume = useCallback(async () => {
    for (let attempt = 0; attempt < RESUME_POLL_ATTEMPTS; attempt += 1) {
      const snapshot = await refreshGenerationActivity();
      if (!mountedRef.current) return;
      const resumed = snapshot ? findResumedGeneration(snapshot.items, operationId) : null;
      if (resumed) {
        router.push(presentationHref(resumed.presentationId));
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, RESUME_POLL_MS));
      if (!mountedRef.current) return;
    }
    setPhase("notFound");
  }, [operationId, router]);

  const handleAction = useCallback(async (
    failedOperationId: string,
    action: "retry",
    options: WorkspaceOperationActionOptions,
  ) => {
    await postWorkspaceOperationAction(failedOperationId, {
      ...options,
      action,
      idempotencyKey: options.idempotencyKey ?? crypto.randomUUID(),
    });
    if (!mountedRef.current) return;
    setPhase("resuming");
    void followResume();
  }, [followResume]);

  const classKey = generationFailureKey(failureClass);
  const key = classKey ?? "unknown";
  const resumePrimary = isResumePrimary(failureClass);
  const startNew = <Button key="new" type="button" size="sm"
    variant={resumePrimary ? "outline" : "default"} onClick={() => router.push("/upload")}>
    {t("panel.startNew")}
  </Button>;
  const recovery = resumable && <WorkspacePartialRecovery key="recovery" operationId={operationId}
    disabled={phase !== "idle"} onAction={handleAction} />;

  return <div role="alert" aria-label={t("panel.aria")} data-testid="generation-failure-panel"
    data-failure-class={key} className="mt-3 space-y-2">
    {classKey ? <>
      <p className="font-semibold text-red-800">{t(`${classKey}.title`)}</p>
      <p>{t(`${classKey}.body`)}</p>
    </> : <p>{fallbackText}</p>}
    <div className="flex flex-wrap items-start gap-2">
      {resumePrimary ? [recovery, startNew] : [startNew, recovery]}
    </div>
    {phase === "resuming" && <p role="status" className="text-xs text-slate-700">{t("panel.resuming")}</p>}
    {phase === "notFound" && <p role="status" className="text-xs text-slate-700">
      {t("panel.resumeNotFound")}{" "}
      <Link href="/dashboard" prefetch={false} className="font-medium underline">{t("panel.openDashboard")}</Link>
    </p>}
  </div>;
}
