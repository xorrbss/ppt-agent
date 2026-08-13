import type { AuthoredQualityReviewMetrics } from "../../services/api/authored";
import { useTranslations } from "next-intl";

interface AuthoredQualityOpsDashboardProps {
  metrics: AuthoredQualityReviewMetrics;
  formatDuration: (milliseconds?: number) => string | null;
}

export function AuthoredQualityOpsDashboard({ metrics, formatDuration }: AuthoredQualityOpsDashboardProps) {
  const t = useTranslations("qualityReview.dashboard");
  const latency = metrics.latency_percentiles;
  const usage = metrics.stage_usage;
  const latencyStages = [...new Set([...Object.keys(latency?.cold ?? {}), ...Object.keys(latency?.warm ?? {})])];
  const usageStages = Object.keys(usage ?? {});
  if (!latencyStages.length && !usageStages.length) return null;
  const formatPercentile = (value?: number) => value === undefined ? "—" : formatDuration(value) ?? "—";
  const formatUsage = (value?: number) => value === undefined ? "—" : value.toLocaleString("en-US");
  const formatCost = (value?: number) => value === undefined ? "—" : `$${value.toFixed(4)}`;
  const latencyHeadings = ["stage", "coldP50", "coldP95", "coldP99", "warmP50", "warmP95", "warmP99"] as const;
  const usageHeadings = ["stage", "inputTokens", "outputTokens", "totalTokens", "cost", "cacheHitsMisses"] as const;
  return <section data-testid="quality-operations-dashboard" className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 text-xs text-slate-700">
    <div><p className="font-semibold text-slate-900">{t("title")}</p><p className="mt-1 text-slate-600">{t("description")}</p></div>
    {latencyStages.length > 0 && <div className="overflow-x-auto"><p className="mb-2 font-medium text-slate-900">{t("latencyTitle")}</p><table className="w-full min-w-[620px] text-left"><thead className="border-b text-slate-500"><tr>{latencyHeadings.map((key) => <th key={key} className="pb-1 pr-3 last:pr-0">{t(key)}</th>)}</tr></thead><tbody>{latencyStages.map((stage) => <tr key={stage} className="border-b last:border-0"><td className="py-1.5 pr-3 font-medium">{stage}</td>{(["cold", "cold", "cold", "warm", "warm", "warm"] as const).map((temperature, index) => <td key={`${temperature}-${index}`} className="py-1.5 pr-3 last:pr-0">{formatPercentile(latency?.[temperature]?.[stage]?.[["p50_ms", "p95_ms", "p99_ms"][index % 3] as "p50_ms" | "p95_ms" | "p99_ms"])}</td>)}</tr>)}</tbody></table></div>}
    {usageStages.length > 0 && <div className="overflow-x-auto"><p className="mb-2 font-medium text-slate-900">{t("usageTitle")}</p><table className="w-full min-w-[620px] text-left"><thead className="border-b text-slate-500"><tr>{usageHeadings.map((key) => <th key={key} className="pb-1 pr-3 last:pr-0">{t(key)}</th>)}</tr></thead><tbody>{usageStages.map((stage) => { const row = usage?.[stage]; return <tr key={stage} className="border-b last:border-0"><td className="py-1.5 pr-3 font-medium">{stage}</td><td className="py-1.5 pr-3">{formatUsage(row?.input_tokens)}</td><td className="py-1.5 pr-3">{formatUsage(row?.output_tokens)}</td><td className="py-1.5 pr-3">{formatUsage(row?.total_tokens)}</td><td className="py-1.5 pr-3">{formatCost(row?.cost_usd)}</td><td className="py-1.5">{formatUsage(row?.cache_hits)} / {formatUsage(row?.cache_misses)}</td></tr>; })}</tbody></table></div>}
  </section>;
}
