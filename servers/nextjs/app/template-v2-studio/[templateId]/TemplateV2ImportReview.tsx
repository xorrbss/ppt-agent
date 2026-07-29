import { buildTemplateV2ImportReview } from "@/lib/template-v2-import-review";

const OUTCOME_LABELS = {
  "editable-text": "Editable text",
  "editable-container": "Editable shape",
  "manual-review": "Manual review",
} as const;

export default function TemplateV2ImportReview({
  analysis,
}: {
  analysis: unknown;
}) {
  const review = buildTemplateV2ImportReview(analysis);
  if (!review || review.total === 0) return null;

  return (
    <details className="rounded border border-slate-800 p-3">
      <summary className="cursor-pointer text-xs font-semibold text-slate-200">
        Review candidate differences ({review.total})
      </summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-xs">
          <thead className="text-slate-400">
            <tr>
              <th className="py-1 pr-3">Slide</th>
              <th className="py-1 pr-3">Candidate</th>
              <th className="py-1 pr-3">Geometry</th>
              <th className="py-1 pr-3">Confidence</th>
              <th className="py-1">Import result</th>
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
                  <span className="block">{row.name}</span>
                  <span className="text-slate-500">
                    {row.sourceId} · {row.kind}
                  </span>
                </td>
                <td className="py-1 pr-3 font-mono">{row.geometry}</td>
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
                    {OUTCOME_LABELS[row.outcome]}
                  </span>
                  {row.reason ? (
                    <span className="ml-1 text-slate-400">({row.reason})</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {review.truncated ? (
          <p className="mt-2 text-xs text-amber-200">
            Showing the first {review.rows.length} candidates.
          </p>
        ) : null}
      </div>
    </details>
  );
}
