"use client";

import { Button } from "@/components/ui/button";
import { useTranslations } from "next-intl";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type {
  HybridExportHistory,
  HybridExportHistoryAttempt,
} from "../../../services/api/hybrid-export-history";

const HybridExportHistoryDialog = ({
  history,
  loading,
  error,
  onClose,
}: {
  history: HybridExportHistory | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) => {
  const t = useTranslations("presentationHybridHistory");
  const displayDigest = (value: string | undefined) => value ?? t("notRecorded");
  const resolutionLabel = (attempt: HybridExportHistoryAttempt) =>
    attempt.resolution === "resolved" ? t("resolved")
      : attempt.resolution === "superseded" ? t("superseded")
        : attempt.state === "succeeded" ? t("currentSuccess") : t("unresolvedFailure");
  const visualParityLabel = (value: HybridExportHistoryAttempt["evidence"]["visualParity"]) =>
    value === "preflight" ? t("preflight")
      : value === "passed" ? t("passed")
        : value === "failed" ? t("failed") : t("unverified");
  return (
  <Dialog
    open={loading || history !== null || error !== null}
    onOpenChange={(open) => !open && onClose()}
  >
    <DialogContent
      className="max-h-[min(720px,calc(100vh-2rem))] overflow-y-auto sm:max-w-3xl"
      data-testid="hybrid-export-history-dialog"
    >
      <DialogHeader>
        <DialogTitle>{t("title")}</DialogTitle>
        <DialogDescription>
          {t("description")}
        </DialogDescription>
      </DialogHeader>
      {loading ? <p className="text-sm text-slate-600">{t("loading")}</p> : null}
      {error ? <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{error}</p> : null}
      {history ? (
        <div className="space-y-3 text-sm text-slate-700">
          <p className="rounded-lg border border-slate-200 p-3 text-xs">
            {t("retention", { attempts: history.retention.attemptRetentionDays ?? t("notRecorded"), evidence: history.retention.evidenceRetentionDays ?? t("notRecorded") })}
            {history.retention.policy ? t("policy", { value: history.retention.policy }) : ""}
            {history.retention.deletesResolvedAttempts === false ? t("resolvedPreserved") : ""}
            {history.retention.expiryAction ? t("expiryAction", { action: history.retention.expiryAction }) : ""}
            {history.retention.deletionAuthorized === false ? t("deletionUnauthorized") : ""}
            {history.retention.expiresAt ? t("policyExpiry", { date: history.retention.expiresAt }) : ""}
          </p>
          {history.attempts.length === 0 ? <p>{t("noHistory")}</p> : null}
          {history.attempts.map((attempt) => (
            <section key={attempt.exportAttemptId} className="rounded-lg border border-slate-200 p-3">
              <p className="font-medium">
                {t("revisionStatus", { revision: attempt.presentationRevision, status: resolutionLabel(attempt) })}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {t("attemptStatus", { id: attempt.exportAttemptId, status: attempt.state })}
                {attempt.completedAt ? t("completedAt", { date: attempt.completedAt }) : ""}
                {attempt.failureCode ? t("failureCode", { code: attempt.failureCode }) : ""}
              </p>
              {attempt.supersededByAttemptId ? (
                <p className="mt-1 text-xs text-slate-500">{t("subsequentAttempt", { id: attempt.supersededByAttemptId })}</p>
              ) : null}
              <div className="mt-3 rounded-md bg-slate-50 p-2 text-xs">
                <p>{t("evidenceStatus", { status: attempt.evidence.status, parity: visualParityLabel(attempt.evidence.visualParity) })}</p>
                <p className="mt-1 break-all">{t("canonical", { digest: displayDigest(attempt.evidence.canonicalSourceDigest) })}</p>
                <p className="mt-1 break-all">{t("renderer", { digest: displayDigest(attempt.evidence.rendererSourceDigest) })}</p>
                <p className="mt-1 break-all">{t("pptx", { digest: displayDigest(attempt.evidence.pptxSha256) })}</p>
                <p className="mt-1 break-all">{t("manifestDigests", { bundle: displayDigest(attempt.evidence.bundleManifestSha256), render: displayDigest(attempt.evidence.renderManifestSha256), report: displayDigest(attempt.evidence.reportManifestSha256) })}</p>
                <p className="mt-1">{t("renderCoverage", { count: attempt.evidence.slidePngSha256.length, manifest: displayDigest(attempt.evidence.coverageManifestSha256) })}</p>
                {attempt.evidence.expiresAt ? <p className="mt-1 text-amber-800">{t("evidenceExpiry", { date: attempt.evidence.expiresAt })}</p> : null}
              </div>
              {attempt.evidence.visualParity !== "passed" ? (
                <p className="mt-2 text-xs text-amber-800">
                  {attempt.evidence.visualParity === "failed"
                    ? t("promotionFailed")
                    : t("promotionUnverified")}
                </p>
              ) : null}
              <p className="mt-2 text-xs">
                {t("nativeCounts", { text: attempt.evidence.nativeText ?? t("notRecorded"), image: attempt.evidence.nativeImage ?? t("notRecorded"), shape: attempt.evidence.nativeShape ?? t("notRecorded"), table: attempt.evidence.nativeTable ?? t("notRecorded"), chart: attempt.evidence.nativeChart ?? t("notRecorded"), raster: attempt.evidence.rasterRemaining ?? t("notRecorded") })}
              </p>
              {attempt.evidence.localFitFallbackPages?.length ? (
                <p className="mt-1 text-xs text-amber-800">
                  {t("fallbackPages", { pages: attempt.evidence.localFitFallbackPages.join(", ") })}
                </p>
              ) : null}
              {attempt.evidence.typographyIssues?.length || attempt.evidence.fallbackReasons?.length ? (
                <p className="mt-1 text-xs text-amber-800">
                  {t("typographyFallback", { reasons: [...(attempt.evidence.typographyIssues ?? []), ...(attempt.evidence.fallbackReasons ?? [])].join(", ") })}
                </p>
              ) : (
                <p className="mt-1 text-xs text-slate-500">{t("noTypographyFallback")}</p>
              )}
            </section>
          ))}
        </div>
      ) : null}
      <DialogFooter><Button type="button" onClick={onClose}>{t("confirm")}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
  );
};

export default HybridExportHistoryDialog;
