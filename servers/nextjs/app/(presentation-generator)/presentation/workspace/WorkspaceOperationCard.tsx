import { useEffect, useState } from "react";
import {
  downloadWorkspaceArtifact,
  WorkspaceApiError,
} from "../../services/api/workspace";
import { KNOWN_OPERATION_STATUSES, type WorkspaceOperationAnchor } from "./types";

const STATUS_LABELS: Record<string, string> = {
  queued: "대기 중",
  waiting_for_assets: "파일 준비 대기 중",
  blocked: "사용자 조치 필요",
  running: "진행 중",
  approval_required: "승인 필요",
  awaiting_approval: "승인 대기 중",
  cancelling: "취소 중",
  succeeded: "완료",
  failed: "실패",
  cancelled: "취소됨",
  dead_letter: "재시도 필요",
};

const TERMINAL = new Set(["succeeded", "failed", "cancelled", "dead_letter"]);
const APPROVABLE = new Set(["approval_required", "awaiting_approval"]);
const RETRYABLE = new Set(["failed", "cancelled", "dead_letter", "blocked"]);
const CANCELLABLE = new Set([
  "queued",
  "waiting_for_assets",
  "blocked",
  "running",
  "approval_required",
  "awaiting_approval",
]);

interface WorkspaceOperationCardProps {
  operation: WorkspaceOperationAnchor;
  currentRevision?: number;
  disabled?: boolean;
  onAction: (operationId: string, action: "retry" | "cancel" | "approve") => void;
  onReExport?: (operationType: "export.pptx" | "export.pdf") => void;
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function numberValue(...values: unknown[]): number | undefined {
  return values.find((value): value is number => typeof value === "number" && Number.isFinite(value));
}

function stringValue(...values: unknown[]): string | undefined {
  return values.find((value): value is string => typeof value === "string" && value.trim().length > 0);
}

function artifactFileName(result: Record<string, unknown>, format: string | undefined): string {
  const candidate = stringValue(
    result.file_name,
    result.filename,
    result.artifact_file_name,
    result.artifactFileName,
  );
  if (candidate) return candidate.replace(/[\\/\u0000-\u001f\u007f]/g, "_");
  return `presentation.${format ?? "bin"}`;
}

function artifactExpired(result: Record<string, unknown>): boolean {
  const status = stringValue(result.artifact_status, result.artifactStatus, result.status);
  if (status === "expired") return true;
  const expiresAt = stringValue(result.expires_at, result.expiresAt);
  return expiresAt !== undefined && Number.isFinite(Date.parse(expiresAt)) && Date.parse(expiresAt) <= Date.now();
}

function triggerBrowserDownload(blob: Blob, fileName: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = fileName;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

function issueLabel(value: unknown): string | undefined {
  const issue = recordValue(value);
  const message = stringValue(issue.message, issue.title, issue.code, issue.reason);
  if (!message) return undefined;
  const slide = numberValue(issue.slide_number, issue.slideNumber, issue.slide_index, issue.slideIndex);
  return slide === undefined ? message : `${slide + (issue.slide_index !== undefined ? 1 : 0)}번 슬라이드 · ${message}`;
}

function useApprovalExpired(expiresAt: string | undefined, approvable: boolean): boolean {
  const expiresAtMs = expiresAt ? Date.parse(expiresAt) : Number.NaN;
  const [expired, setExpired] = useState(
    approvable && Number.isFinite(expiresAtMs) && expiresAtMs <= Date.now(),
  );

  useEffect(() => {
    if (!approvable || !Number.isFinite(expiresAtMs)) {
      setExpired(false);
      return;
    }
    let timeout: number | undefined;
    const scheduleExpiry = () => {
      const remaining = expiresAtMs - Date.now();
      if (remaining <= 0) {
        setExpired(true);
        return;
      }
      setExpired(false);
      // Browsers clamp larger delays; reschedule so a far-future approval is
      // never marked expired immediately because of timer overflow.
      timeout = window.setTimeout(scheduleExpiry, Math.min(remaining, 2_147_483_647));
    };
    scheduleExpiry();
    return () => {
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [approvable, expiresAtMs]);

  return expired;
}

export function WorkspaceOperationCard({
  operation,
  currentRevision,
  disabled = false,
  onAction,
  onReExport,
}: WorkspaceOperationCardProps) {
  const known = (KNOWN_OPERATION_STATUSES as readonly string[]).includes(operation.status);
  const approvalExpired = useApprovalExpired(
    operation.approvalExpiresAt,
    APPROVABLE.has(operation.status),
  );
  const retryUnavailable =
    RETRYABLE.has(operation.status) && operation.retryable === false;
  const progress =
    operation.progress === undefined ? undefined : Math.max(0, Math.min(100, operation.progress));
  const latestMessage = [...operation.events].reverse().find((event) => event.message)?.message;
  const result = recordValue(operation.result);
  const isQualityReview = operation.operationType === "quality.review" || result.kind === "quality.review";
  const isExport = operation.operationType === "export.pptx" || operation.operationType === "export.pdf";
  const isVersionRestore =
    operation.operationType === "version.restore" || result.kind === "version.restore";
  const exportOperationType = isExport
    ? (operation.operationType as "export.pptx" | "export.pdf")
    : undefined;
  const resultSummary = operation.result && !isQualityReview && !isExport && !isVersionRestore
    ? ["summary", "message", "revision", "slide_count"]
        .flatMap((key) => {
          const value = operation.result?.[key];
          return typeof value === "string" || typeof value === "number" ? [`${key}: ${value}`] : [];
        })
        .join(" · ")
    : "";
  const inspectedRevision = numberValue(result.inspected_revision, result.inspectedRevision);
  const reviewedCount = numberValue(result.reviewed_count, result.reviewedCount);
  const issueSlideCount = numberValue(result.issue_slide_count, result.issueSlideCount);
  const remainingIssueCount = numberValue(result.remaining_issue_count, result.remainingIssueCount);
  const qualityIssues = (
    Array.isArray(result.remaining_issues)
      ? result.remaining_issues
      : Array.isArray(result.issues)
        ? result.issues
        : []
  )
    .map(issueLabel)
    .filter((issue): issue is string => Boolean(issue))
    .slice(0, 3);
  const artifactId = isExport ? stringValue(result.artifact_id, result.artifactId) : undefined;
  const artifactFormat = stringValue(result.format, result.artifact_kind)?.toLowerCase();
  const sourceRevision = numberValue(result.source_revision, result.sourceRevision);
  const publishedRevision = numberValue(result.revision, result.result_revision, result.resultRevision);
  const restoredSlideCount = numberValue(result.slide_count, result.slideCount);
  const restoredVersionId = stringValue(
    result.restored_version_id,
    result.restoredVersionId,
  );
  const artifactStale =
    sourceRevision !== undefined &&
    currentRevision !== undefined &&
    currentRevision !== sourceRevision;
  const [downloadExpired, setDownloadExpired] = useState(() => artifactExpired(result));
  const [downloadError, setDownloadError] = useState<string>();
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    setDownloadExpired(artifactExpired(result));
    setDownloadError(undefined);
    const expiresAt = stringValue(result.expires_at, result.expiresAt);
    const expiresAtMs = expiresAt ? Date.parse(expiresAt) : Number.NaN;
    if (!Number.isFinite(expiresAtMs)) return;
    let timeout: number | undefined;
    const scheduleExpiry = () => {
      const remaining = expiresAtMs - Date.now();
      if (remaining <= 0) {
        setDownloadExpired(true);
        return;
      }
      timeout = window.setTimeout(scheduleExpiry, Math.min(remaining, 2_147_483_647));
    };
    scheduleExpiry();
    return () => {
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [operation.operationId, operation.updatedAt, result]);

  const handleArtifactDownload = async () => {
    if (!artifactId || downloading || downloadExpired) return;
    setDownloading(true);
    setDownloadError(undefined);
    try {
      const download = await downloadWorkspaceArtifact(artifactId);
      triggerBrowserDownload(
        download.blob,
        download.fileName ?? artifactFileName(result, artifactFormat),
      );
    } catch (error) {
      const expired =
        error instanceof WorkspaceApiError &&
        (error.status === 410 ||
          error.code === "workspace_export_expired" ||
          error.code === "workspace_export_missing");
      if (expired) {
        setDownloadExpired(true);
      } else {
        setDownloadError(
          error instanceof Error
            ? error.message
            : "다운로드를 시작할 수 없습니다.",
        );
      }
    } finally {
      setDownloading(false);
    }
  };

  if (!known) {
    return (
      <article className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm" role="alert">
        <p className="font-semibold text-amber-900">지원되지 않는 작업 상태</p>
        <p className="mt-1 text-amber-800">새로고침 후에도 계속되면 관리자에게 문의해 주세요.</p>
      </article>
    );
  }

  return (
    <article
      className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm"
      aria-label={`${operation.title} 작업`}
      data-operation-id={operation.operationId}
      data-operation-status={operation.status}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-slate-900">{operation.title}</h3>
          <p className="mt-0.5 text-xs text-slate-500" role="status" aria-live="polite">
            {STATUS_LABELS[operation.status]}
          </p>
        </div>
        {!TERMINAL.has(operation.status) && !APPROVABLE.has(operation.status) && (
          <span
            aria-hidden="true"
            className="mt-1 h-2 w-2 shrink-0 animate-pulse rounded-full bg-blue-500 motion-reduce:animate-none"
          />
        )}
      </div>
      {progress !== undefined && (
        <div className="mt-3">
          <div
            className="h-1.5 overflow-hidden rounded-full bg-slate-100"
            role="progressbar"
            aria-label="작업 진행률"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress)}
          >
            <div
              className="h-full rounded-full bg-blue-500 transition-[width] motion-reduce:transition-none"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="mt-1 text-right text-[11px] text-slate-500">{Math.round(progress)}%</p>
        </div>
      )}
      {latestMessage && <p className="mt-2 text-xs text-slate-600">{latestMessage}</p>}
      {operation.status === "succeeded" && resultSummary && (
        <p className="mt-2 rounded-lg bg-emerald-50 px-2.5 py-2 text-xs text-emerald-800">
          {resultSummary}
        </p>
      )}
      {operation.status === "succeeded" && isVersionRestore && (
        <section
          className="mt-2 rounded-lg border border-emerald-100 bg-emerald-50 px-2.5 py-2 text-xs text-emerald-950"
          aria-label="버전 복원 결과"
          data-testid="version-restore-result"
        >
          <p className="font-semibold">버전 복원이 완료되었습니다.</p>
          <p className="mt-1 text-emerald-800">
            {sourceRevision !== undefined && publishedRevision !== undefined
              ? `리비전 ${sourceRevision} → ${publishedRevision}`
              : publishedRevision !== undefined
                ? `리비전 ${publishedRevision}`
                : "새 리비전으로 게시됨"}
            {restoredSlideCount !== undefined ? ` · ${restoredSlideCount}장` : ""}
          </p>
          {restoredVersionId && (
            <p className="mt-1 truncate text-emerald-700" title={restoredVersionId}>
              대상 버전 {restoredVersionId}
            </p>
          )}
        </section>
      )}
      {operation.status === "succeeded" && isQualityReview && (
        <section
          className="mt-2 rounded-lg border border-blue-100 bg-blue-50 px-2.5 py-2 text-xs text-blue-950"
          aria-label="품질 검토 결과"
          data-testid="quality-review-result"
        >
          <p className="font-semibold">{stringValue(result.message) ?? "읽기 전용 품질 검토가 완료되었습니다."}</p>
          <p className="mt-1 text-blue-800">
            {reviewedCount !== undefined ? `검토 ${reviewedCount}장` : "검토 완료"}
            {issueSlideCount !== undefined ? ` · 이슈 슬라이드 ${issueSlideCount}장` : ""}
            {remainingIssueCount !== undefined ? ` · 남은 이슈 ${remainingIssueCount}개` : ""}
            {inspectedRevision !== undefined ? ` · 검토 리비전 ${inspectedRevision}` : ""}
          </p>
          {qualityIssues.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-4">
              {qualityIssues.map((issue, index) => <li key={`${index}-${issue}`}>{issue}</li>)}
            </ul>
          )}
        </section>
      )}
      {operation.status === "succeeded" && isExport && (
        <section
          className="mt-2 rounded-lg border border-emerald-100 bg-emerald-50 px-2.5 py-2 text-xs text-emerald-950"
          aria-label="내보내기 결과"
          data-testid="export-artifact-result"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate font-semibold">{artifactFileName(result, artifactFormat)}</p>
              <p className="mt-0.5 text-emerald-800">
                {artifactFormat?.toUpperCase() ?? "파일"}
                {sourceRevision !== undefined ? ` · 소스 리비전 ${sourceRevision}` : ""}
              </p>
            </div>
            <button
              type="button"
              data-testid="artifact-download"
              disabled={disabled || downloading || downloadExpired || !artifactId}
              onClick={() => void handleArtifactDownload()}
              className="shrink-0 rounded-lg bg-emerald-700 px-3 py-1.5 font-semibold text-white hover:bg-emerald-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
              aria-label={`${artifactFormat?.toUpperCase() ?? "내보낸"} 파일 다운로드`}
            >
              다운로드
            </button>
          </div>
          {!artifactId && (
            <p className="mt-2 rounded-md bg-red-100 px-2 py-1.5 text-red-800" role="alert">
              다운로드 정보를 확인할 수 없습니다. 다시 내보내 주세요.
            </p>
          )}
          {downloadError && (
            <p className="mt-2 rounded-md bg-red-100 px-2 py-1.5 text-red-800" role="alert">
              {downloadError}
            </p>
          )}
          {downloadExpired && (
            <div className="mt-2 rounded-md bg-amber-100 px-2 py-2 text-amber-950" data-testid="artifact-expired">
              <p role="status">내보내기 파일이 만료되었습니다.</p>
              {exportOperationType && onReExport && (
                <button
                  type="button"
                  data-testid="artifact-reexport"
                  disabled={disabled}
                  onClick={() => onReExport(exportOperationType)}
                  className="mt-2 rounded-lg border border-amber-700 bg-white px-3 py-1.5 font-semibold text-amber-950 hover:bg-amber-50 disabled:opacity-50"
                  aria-label={`최신 리비전을 ${artifactFormat?.toUpperCase() ?? "파일"}로 다시 내보내기`}
                >
                  최신 리비전으로 다시 내보내기
                </button>
              )}
            </div>
          )}
          {artifactStale && (
            <p className="mt-2 rounded-md bg-amber-100 px-2 py-1.5 text-amber-900" role="status" data-testid="artifact-stale">
              이 파일은 이전 리비전에서 생성되었습니다. 최신 내용이 필요하면 다시 내보내세요.
            </p>
          )}
        </section>
      )}
      {operation.error && (
        <p className="mt-2 rounded-lg bg-red-50 px-2.5 py-2 text-xs text-red-700" role="alert">
          {operation.error}
        </p>
      )}
      {approvalExpired && (
        <p className="mt-2 rounded-lg bg-amber-50 px-2.5 py-2 text-xs text-amber-800" role="status">
          승인 기한이 만료되었습니다. 작업을 다시 시도해 주세요.
        </p>
      )}
      {retryUnavailable && (
        <p
          className="mt-2 rounded-lg bg-amber-50 px-2.5 py-2 text-xs text-amber-800"
          role="status"
          data-testid="operation-retry-unavailable"
        >
          이 오류는 재시도할 수 없습니다. 내용을 확인한 뒤 새 명령을 보내 주세요.
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {RETRYABLE.has(operation.status) && !retryUnavailable && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => onAction(operation.operationId, "retry")}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            다시 시도
          </button>
        )}
        {APPROVABLE.has(operation.status) && (
          <button
            type="button"
            disabled={disabled || approvalExpired}
            data-testid="operation-approve"
            onClick={() => onAction(operation.operationId, "approve")}
            className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            승인
          </button>
        )}
        {CANCELLABLE.has(operation.status) && (
          <button
            type="button"
            disabled={disabled}
            data-testid="operation-cancel"
            onClick={() => onAction(operation.operationId, "cancel")}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            취소
          </button>
        )}
      </div>
    </article>
  );
}
