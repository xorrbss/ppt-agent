import { useTranslations } from "next-intl";
import { isRecord } from "@/lib/guards";

const statusKeys: Record<string, string> = {
  available: "svgStatusAvailable", unavailable: "svgStatusUnavailable",
  passed: "svgStatusPassed", failed: "svgStatusFailed", stale: "svgStatusStale",
  unmeasured: "svgStatusUnmeasured", current: "svgStatusCurrent",
  partial: "svgStatusPartial", flagged: "svgStatusFlagged", clear: "svgStatusClear",
};

export default function SvgQualityStatusSummary({ status }: { status: unknown }) {
  const t = useTranslations("presentationOperationCard");
  if (!isRecord(status)) return null;
  const value = (key: string) => {
    const domain = status[key];
    return t(statusKeys[String(isRecord(domain) ? domain.status : domain)] ?? "svgStatusUnavailable");
  };
  const fidelity = isRecord(status) ? status.currentArtifactFidelity : undefined;
  const fidelityStatus = fidelity && typeof fidelity === "object"
    ? (fidelity as Record<string, unknown>).status : undefined;
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-blue-800" data-testid="svg-quality-status">
      <dt>{t("svgArtifactStatus")}</dt><dd>{value("artifact")}</dd>
      <dt>{t("svgMeasuredStatus")}</dt><dd>{value("measured")}</dd>
      <dt>{t("svgSemanticStatus")}</dt><dd>{value("semanticCurrent")}</dd>
      <dt>{t("svgDesignStatus")}</dt><dd>{value("designFindings")}</dd>
      {(fidelityStatus === "passed" || fidelityStatus === "failed") && <>
        <dt>현재 PPTX 검토</dt>
        <dd>{fidelityStatus === "passed" ? "통과" : "확인 필요"}</dd>
      </>}
    </dl>
  );
}
