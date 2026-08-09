"use client";

import { useTranslations } from "next-intl";

import {
  buildTemplateV2ImportReview,
  type TemplateV2ImportReviewOutcome,
} from "@/lib/template-v2-import-review";

export default function TemplateV2ImportReview({
  analysis,
}: {
  analysis: unknown;
}) {
  const t = useTranslations("studio");
  const review = buildTemplateV2ImportReview(analysis);
  if (!review || review.total === 0) return null;

  function outcomeLabel(outcome: TemplateV2ImportReviewOutcome) {
    if (outcome === "editable-text") {
      return t("importReview.outcomes.editableText");
    }
    if (outcome === "editable-container") {
      return t("importReview.outcomes.editableShape");
    }
    return t("importReview.outcomes.manualReview");
  }

  function kindLabel(kind: string) {
    if (kind === "text") return t("importReview.kinds.text");
    if (kind === "container") return t("importReview.kinds.container");
    if (kind === "unsupported") return t("importReview.kinds.unsupported");
    return t("importReview.kinds.unknown");
  }

  return (
    <details className="rounded border border-slate-800 p-3">
      <summary className="cursor-pointer text-xs font-semibold text-slate-200">
        {t("importReview.summary", { count: review.total })}
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-xs">
          <thead className="text-slate-400">
            <tr>
              <th className="py-1 pr-3">{t("importReview.slide")}</th>
              <th className="py-1 pr-3">{t("importReview.candidate")}</th>
              <th className="py-1 pr-3">{t("importReview.geometry")}</th>
              <th className="py-1 pr-3">{t("importReview.confidence")}</th>
              <th className="py-1">{t("importReview.result")}</th>
            </tr>
          </thead>
          <tbody>
            {review.rows.map((row) => (
              <tr
                key={`${row.slide}:${row.sourceId}`}
                className="border-t border-slate-800"
              >
                <td className="py-1 pr-3">{row.slide}</td>
                <td className="py-1 pr-3">
                  <span className="block">
                    {row.name === "Unnamed shape"
                      ? t("importReview.unnamedShape")
                      : row.name}
                  </span>
                  <span className="text-slate-500">
                    {row.sourceId} · {kindLabel(row.kind)}
                  </span>
                </td>
                <td className="py-1 pr-3 font-mono">
                  {row.geometry === "unknown"
                    ? t("importReview.unknownGeometry")
                    : row.geometry}
                </td>
                <td className="py-1 pr-3">
                  {(row.confidence * 100).toFixed(0)}%
                </td>
                <td className="py-1">
                  <span
                    className={
                      row.outcome === "manual-review"
                        ? "text-amber-200"
                        : "text-emerald-200"
                    }
                  >
                    {outcomeLabel(row.outcome)}
                  </span>
                  {row.reason ? (
                    <span className="ml-1 text-slate-400">
                      ({t("importReview.unsupportedReason")})
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {review.truncated ? (
          <p className="mt-2 text-xs text-amber-200">
            {t("importReview.showingFirst", { count: review.rows.length })}
          </p>
        ) : null}
      </div>
    </details>
  );
}
