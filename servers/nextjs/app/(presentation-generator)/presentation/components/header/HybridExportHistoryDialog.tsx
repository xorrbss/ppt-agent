"use client";

import { Button } from "@/components/ui/button";
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

function displayDigest(value: string | undefined): string {
  return value ?? "미기록";
}

function resolutionLabel(attempt: HybridExportHistoryAttempt): string {
  if (attempt.resolution === "resolved") return "후속 성공으로 해결됨";
  if (attempt.resolution === "superseded") return "후속 성공으로 대체됨";
  return attempt.state === "succeeded" ? "현재 성공" : "해결되지 않은 실패";
}

function visualParityLabel(value: HybridExportHistoryAttempt["evidence"]["visualParity"]): string {
  if (value === "preflight") return "사전 점검만 완료";
  if (value === "passed") return "검증 통과";
  if (value === "failed") return "검증 실패";
  return "시각 동일성 미검증";
}

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
}) => (
  <Dialog
    open={loading || history !== null || error !== null}
    onOpenChange={(open) => !open && onClose()}
  >
    <DialogContent
      className="max-h-[min(720px,calc(100vh-2rem))] overflow-y-auto sm:max-w-3xl"
      data-testid="hybrid-export-history-dialog"
    >
      <DialogHeader>
        <DialogTitle>Hybrid 내보내기 이력 및 증거</DialogTitle>
        <DialogDescription>
          같은 revision·형식의 후속 성공은 이전 실패를 삭제하지 않고 해결됨으로 표시합니다. 이력·다운로드·구조 검사만으로 promotion 또는 배포 승인이 되지 않습니다.
        </DialogDescription>
      </DialogHeader>
      {loading ? <p className="text-sm text-slate-600">이력을 불러오는 중…</p> : null}
      {error ? <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{error}</p> : null}
      {history ? (
        <div className="space-y-3 text-sm text-slate-700">
          <p className="rounded-lg border border-slate-200 p-3 text-xs">
            attempt 보존 {history.retention.attemptRetentionDays ?? "미기록"}일 · 증거 bundle 보존 {history.retention.evidenceRetentionDays ?? "미기록"}일
            {history.retention.policy ? ` · 정책 ${history.retention.policy}` : ""}
            {history.retention.deletesResolvedAttempts === false ? " · 해결된 과거 시도 삭제 안 함" : ""}
            {history.retention.expiryAction ? ` · 만료 처리 ${history.retention.expiryAction}` : ""}
            {history.retention.deletionAuthorized === false ? " · 삭제 권한 없음" : ""}
            {history.retention.expiresAt ? ` · 정책 만료 ${history.retention.expiresAt}` : ""}
          </p>
          {history.attempts.length === 0 ? <p>기록된 Hybrid 내보내기가 없습니다.</p> : null}
          {history.attempts.map((attempt) => (
            <section key={attempt.exportAttemptId} className="rounded-lg border border-slate-200 p-3">
              <p className="font-medium">
                revision {attempt.presentationRevision} · {resolutionLabel(attempt)}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                attempt {attempt.exportAttemptId} · {attempt.state}
                {attempt.completedAt ? ` · 완료 ${attempt.completedAt}` : ""}
                {attempt.failureCode ? ` · 실패 사유 ${attempt.failureCode}` : ""}
              </p>
              {attempt.supersededByAttemptId ? (
                <p className="mt-1 text-xs text-slate-500">후속 attempt: {attempt.supersededByAttemptId}</p>
              ) : null}
              <div className="mt-3 rounded-md bg-slate-50 p-2 text-xs">
                <p>증거 상태: {attempt.evidence.status} · visualParity: {visualParityLabel(attempt.evidence.visualParity)} · promotion 근거 아님</p>
                <p className="mt-1 break-all">canonical: {displayDigest(attempt.evidence.canonicalSourceDigest)}</p>
                <p className="mt-1 break-all">renderer: {displayDigest(attempt.evidence.rendererSourceDigest)}</p>
                <p className="mt-1 break-all">PPTX: {displayDigest(attempt.evidence.pptxSha256)}</p>
                <p className="mt-1 break-all">bundle/render/quality(report) manifest: {displayDigest(attempt.evidence.bundleManifestSha256)} / {displayDigest(attempt.evidence.renderManifestSha256)} / {displayDigest(attempt.evidence.reportManifestSha256)}</p>
                <p className="mt-1">render PNG digest {attempt.evidence.slidePngSha256.length}개 · coverage manifest {displayDigest(attempt.evidence.coverageManifestSha256)}</p>
                {attempt.evidence.expiresAt ? <p className="mt-1 text-amber-800">증거 만료: {attempt.evidence.expiresAt}</p> : null}
              </div>
              {attempt.evidence.visualParity !== "passed" ? (
                <p className="mt-2 text-xs text-amber-800">
                  {attempt.evidence.visualParity === "failed"
                    ? "이 attempt는 PowerPoint 시각 검증 실패로 release promotion에 사용할 수 없습니다."
                    : "PowerPoint 시각 검증이 완료되지 않았으므로 이 attempt는 release promotion에 사용할 수 없습니다."}
                </p>
              ) : null}
              <p className="mt-2 text-xs">
                실제 native: 텍스트 {attempt.evidence.nativeText ?? "미기록"} · 이미지 {attempt.evidence.nativeImage ?? "미기록"} · 도형 {attempt.evidence.nativeShape ?? "미기록"} · 표 {attempt.evidence.nativeTable ?? "미기록"} · 차트 {attempt.evidence.nativeChart ?? "미기록"} · raster {attempt.evidence.rasterRemaining ?? "미기록"}
              </p>
              {attempt.evidence.typographyIssues?.length || attempt.evidence.fallbackReasons?.length ? (
                <p className="mt-1 text-xs text-amber-800">
                  typography/fallback: {[...(attempt.evidence.typographyIssues ?? []), ...(attempt.evidence.fallbackReasons ?? [])].join(", ")}
                </p>
              ) : (
                <p className="mt-1 text-xs text-slate-500">typography/fallback 사유는 이 증거에 기록되지 않았습니다.</p>
              )}
            </section>
          ))}
        </div>
      ) : null}
      <DialogFooter><Button type="button" onClick={onClose}>확인</Button></DialogFooter>
    </DialogContent>
  </Dialog>
);

export default HybridExportHistoryDialog;
