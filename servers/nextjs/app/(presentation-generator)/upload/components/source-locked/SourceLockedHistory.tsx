"use client";

import { Button } from "@/components/ui/button";
import type { SourceLockedHistoryEntryV1 } from "../../../services/api/source-locked/history";

const statusLabel: Record<SourceLockedHistoryEntryV1["status"], string> = {
  queued: "대기 중",
  running: "생성 중",
  succeeded: "검증 완료",
  failed: "실패",
  cancelled: "취소됨",
};

/** Source-locked-only browser dashboard; it intentionally does not use Presentation cards. */
export function SourceLockedHistory({
  entries,
  onCancel,
}: {
  entries: readonly SourceLockedHistoryEntryV1[];
  onCancel: (jobId: string) => void;
}) {
  if (entries.length === 0) return null;
  return (
    <section className="mt-4 rounded-xl border border-[#E4E6E9] bg-white px-3 py-3" data-testid="source-locked-history">
      <h2 className="text-sm font-semibold text-[#242629]">원본 문서 그대로 생성 기록</h2>
      <ul className="mt-2 space-y-2" aria-label="원본 문서 생성 기록">
        {entries.map((entry) => {
          const active = entry.status === "queued" || entry.status === "running";
          return (
            <li key={entry.job_id} className="rounded-lg bg-[#FAFAFB] px-3 py-2 text-xs text-[#5E626A]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="max-w-[20rem] truncate font-medium text-[#33363A]" title={entry.source_filename}>
                  {entry.source_filename}
                </span>
                <span>{statusLabel[entry.status]}</span>
              </div>
              {entry.summary ? (
                <p className="mt-1">{entry.summary.source_page_count}장 → {entry.summary.output_slide_count}슬라이드</p>
              ) : null}
              {entry.error?.message ? <p className="mt-1 text-red-600">{entry.error.message}</p> : null}
              <div className="mt-2 flex flex-wrap gap-2">
                {active ? (
                  <Button type="button" size="sm" variant="outline" onClick={() => onCancel(entry.job_id)} data-testid={`source-locked-cancel-${entry.job_id}`}>
                    취소
                  </Button>
                ) : null}
                {entry.status === "succeeded" && entry.artifact ? (
                  <a href={entry.artifact.download_url} className="font-semibold text-[#5141E5] underline" data-testid={`source-locked-history-download-${entry.job_id}`}>
                    PPTX 다운로드
                  </a>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
