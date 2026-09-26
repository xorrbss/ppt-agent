import { useTranslations } from "next-intl";
import type { QualityReviewHistoryItem } from "./WorkspaceOperationResult";
import { qualityRejectionLabel } from "./qualityRejectionLabels";

export function QualityReviewHistoryList({
  items,
}: {
  items: QualityReviewHistoryItem[];
}) {
  const t = useTranslations("presentationQualityHistory");
  if (items.length === 0) return null;
  return (
    <section
      className="mt-3 border-t border-blue-100 pt-2"
      aria-label={t("title")}
    >
      <h4 className="font-semibold">{t("title")}</h4>
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
                  ? t("unknownPage")
                  : t("slide", { index: item.slideIndex + 1 })}
              </p>
              <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px] text-blue-900">
                {item.issueType && (
                  <>
                    <dt>{t("issueType")}</dt>
                    <dd>{item.issueType}</dd>
                  </>
                )}
                {item.severity && (
                  <>
                    <dt>{t("severity")}</dt>
                    <dd>{item.severity}</dd>
                  </>
                )}
                {item.description && (
                  <>
                    <dt>{t("description")}</dt>
                    <dd>{item.description}</dd>
                  </>
                )}
                {item.action && (
                  <>
                    <dt>{t("action")}</dt>
                    <dd>{item.action}</dd>
                  </>
                )}
                {item.applied !== undefined && (
                  <>
                    <dt>{t("appliedLabel")}</dt>
                    <dd>{item.applied ? t("applied") : t("notApplied")}</dd>
                  </>
                )}
                {item.reason && (
                  <>
                    <dt>{t("rejectionReason")}</dt>
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
