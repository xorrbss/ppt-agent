import type { QualityReviewHistoryItem } from "./WorkspaceOperationResult";
import { qualityRejectionLabel } from "./qualityRejectionLabels";

export function QualityReviewHistoryList({
  items,
}: {
  items: QualityReviewHistoryItem[];
}) {
  if (items.length === 0) return null;
  return (
    <section
      className="mt-3 border-t border-blue-100 pt-2"
      aria-label="페이지별 검수 이력"
    >
      <h4 className="font-semibold">페이지별 검수 이력</h4>
      <ol className="mt-1.5 space-y-2">
        {items.map((item, index) => {
          const label = item.reason
            ? qualityRejectionLabel(item.reason)
            : undefined;
          return (
            <li
              key={`${item.slideIndex ?? "unknown"}-${item.issueType ?? index}-${index}`}
              className="rounded-md bg-white/70 px-2 py-1.5"
            >
              <p className="font-medium">
                {item.slideIndex === undefined
                  ? "페이지 정보 없음"
                  : `${item.slideIndex + 1}번 슬라이드`}
              </p>
              <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px] text-blue-900">
                {item.issueType && (
                  <>
                    <dt>이슈 유형</dt>
                    <dd>{item.issueType}</dd>
                  </>
                )}
                {item.severity && (
                  <>
                    <dt>심각도</dt>
                    <dd>{item.severity}</dd>
                  </>
                )}
                {item.description && (
                  <>
                    <dt>설명</dt>
                    <dd>{item.description}</dd>
                  </>
                )}
                {item.action && (
                  <>
                    <dt>수정 조치</dt>
                    <dd>{item.action}</dd>
                  </>
                )}
                {item.applied !== undefined && (
                  <>
                    <dt>적용</dt>
                    <dd>{item.applied ? "적용됨" : "미적용"}</dd>
                  </>
                )}
                {item.reason && (
                  <>
                    <dt>미수정 사유</dt>
                    {/* The raw code stays visible: it is what the logs and the
                        pipeline telemetry are keyed by. */}
                    <dd data-testid="quality-rejection-reason">
                      {label ?? item.reason}
                      {label && (
                        <span className="ml-1 text-blue-500">
                          ({item.reason})
                        </span>
                      )}
                    </dd>
                  </>
                )}
              </dl>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
