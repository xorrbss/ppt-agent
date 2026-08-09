"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import {
  TemplateV2QualityInspectorError,
  applyTemplateV2QualityFix,
  inspectTemplateV2Quality,
  previewTemplateV2QualityFix,
  type TemplateV2QualityApplyResult,
  type TemplateV2QualityFinding,
  type TemplateV2QualityFixPreview,
  type TemplateV2QualityInspection,
} from "@/lib/template-v2-quality-inspector";
import type { JsonRecord } from "@/lib/template-v2-studio";

const FINDING_KEYS = {
  TEXT_OVERFLOW: "qualityInspector.findings.textOverflow",
  TEXT_BELOW_9PT: "qualityInspector.findings.textBelow9pt",
  TEXT_LOW_CONTRAST: "qualityInspector.findings.lowTextContrast",
  SLIDE_OVERDENSE: "qualityInspector.findings.overcrowdedSlide",
  CHART_UNIT_UNSPECIFIED: "qualityInspector.findings.chartUnitMissing",
  CHART_LEGEND_MISSING: "qualityInspector.findings.chartLegendMissing",
  TABLE_TOO_MANY_COLUMNS: "qualityInspector.findings.tooManyTableColumns",
  ELEMENT_UNSUPPORTED: "qualityInspector.findings.unsupportedElement",
  ELEMENT_RASTER_ONLY: "qualityInspector.findings.rasterOnlyElement",
} as const satisfies Record<TemplateV2QualityFinding["reasonCode"], string>;

const DETAIL_KEYS = {
  actual: "qualityInspector.details.actual",
  minimum: "qualityInspector.details.minimum",
  actual_length: "qualityInspector.details.actualLength",
  maximum: "qualityInspector.details.maximum",
  background: "qualityInspector.details.background",
  ratio: "qualityInspector.details.ratio",
  required: "qualityInspector.details.required",
  series_count: "qualityInspector.details.seriesCount",
  recommended_maximum: "qualityInspector.details.recommendedMaximum",
  reason: "qualityInspector.details.reason",
} as const;

const SEVERITY_KEYS = {
  error: "qualityInspector.severity.error",
  warning: "qualityInspector.severity.warning",
  info: "qualityInspector.severity.info",
} as const;

function FindingCard({
  finding,
  disabled,
  onPreview,
}: {
  finding: TemplateV2QualityFinding;
  disabled: boolean;
  onPreview(finding: TemplateV2QualityFinding): void;
}) {
  const t = useTranslations("studio");

  function detailValue(value: string | number | boolean) {
    if (typeof value === "boolean") {
      return t(
        value
          ? "qualityInspector.details.trueValue"
          : "qualityInspector.details.falseValue",
      );
    }
    return String(value);
  }

  return (
    <li
      data-testid={`quality-finding-${finding.reasonCode}`}
      className="rounded border border-slate-700 bg-slate-950 p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-slate-100">
            {t(FINDING_KEYS[finding.reasonCode])}
          </p>
          <code className="mt-1 block text-[10px] text-violet-300">
            {finding.reasonCode}
          </code>
        </div>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
            finding.severity === "error"
              ? "bg-red-950 text-red-200"
              : finding.severity === "warning"
                ? "bg-amber-950 text-amber-200"
                : "bg-sky-950 text-sky-200"
          }`}
        >
          {t(SEVERITY_KEYS[finding.severity])}
        </span>
      </div>
      <p className="mt-2 break-all text-[10px] text-slate-500">
        {finding.elementPath.join(" / ")}
      </p>
      {Object.keys(finding.details).length ? (
        <dl className="mt-2 grid grid-cols-2 gap-x-2 text-[10px] text-slate-400">
          {Object.entries(finding.details).map(([key, value]) => (
            <div key={key} className="contents">
              <dt>
                {Object.prototype.hasOwnProperty.call(DETAIL_KEYS, key)
                  ? t(DETAIL_KEYS[key as keyof typeof DETAIL_KEYS])
                  : t("qualityInspector.details.unknown")}
              </dt>
              <dd className="text-right text-slate-300">
                {detailValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {finding.safeFixAvailable ? (
        <button
          type="button"
          disabled={disabled}
          onClick={() => onPreview(finding)}
          className="mt-3 w-full rounded border border-violet-500/60 px-2 py-1.5 text-xs text-violet-200 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t("qualityInspector.previewFix")}
        </button>
      ) : (
        <p className="mt-2 text-[10px] text-slate-500">
          {t("qualityInspector.reviewOnly")}
        </p>
      )}
    </li>
  );
}

export default function TemplateV2QualityInspector({
  layouts,
  revision,
  disabled = false,
  onApply,
}: {
  layouts: JsonRecord;
  revision: number;
  disabled?: boolean;
  onApply(result: TemplateV2QualityApplyResult): void;
}) {
  const t = useTranslations("studio");
  const [inspection, setInspection] =
    useState<TemplateV2QualityInspection | null>(null);
  const [preview, setPreview] = useState<TemplateV2QualityFixPreview | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const counter = useRef(0);

  function valueLabel(value: unknown): string {
    if (value === undefined) return t("qualityInspector.undefinedValue");
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  function errorLabel(caught: unknown): string {
    if (!(caught instanceof TemplateV2QualityInspectorError)) {
      return t("qualityInspector.errors.failedClosed");
    }
    if (
      caught.code === "template_v2_quality_stale_revision" ||
      caught.code === "template_v2_quality_preview_stale" ||
      caught.code === "template_v2_quality_inspection_stale"
    ) {
      return t("qualityInspector.errors.stale");
    }
    return t("qualityInspector.errors.unavailable");
  }

  useEffect(() => {
    setInspection(null);
    setPreview(null);
    setError(null);
    setStatus(null);
  }, [layouts]);

  function runInspection() {
    try {
      const result = inspectTemplateV2Quality(layouts);
      setInspection(result);
      setPreview(null);
      setError(null);
      setStatus(
        result.findings.length
          ? t("qualityInspector.findingsFound", {
              count: result.findings.length,
            })
          : t("qualityInspector.noFindings")
      );
    } catch (caught) {
      setInspection(null);
      setPreview(null);
      setError(errorLabel(caught));
    }
  }

  function previewFix(finding: TemplateV2QualityFinding) {
    if (!inspection) return;
    counter.current += 1;
    const idempotencyKey = `quality:studio:${revision}:${counter.current
      .toString()
      .padStart(4, "0")}`;
    try {
      setPreview(
        previewTemplateV2QualityFix({
          layouts,
          inspection,
          findingId: finding.id,
          expectedRevision: revision,
          idempotencyKey,
        })
      );
      setError(null);
      setStatus(t("qualityInspector.previewReady"));
    } catch (caught) {
      setPreview(null);
      setError(errorLabel(caught));
    }
  }

  function applyFix() {
    if (!preview) return;
    try {
      const result = applyTemplateV2QualityFix({
        layouts,
        preview,
        expectedRevision: revision,
        currentRevision: revision,
        idempotencyKey: preview.idempotencyKey,
      });
      onApply(result);
      setPreview(null);
      setInspection(null);
      setError(null);
      setStatus(t("qualityInspector.fixApplied"));
    } catch (caught) {
      setError(errorLabel(caught));
    }
  }

  return (
    <section
      data-testid="template-v2-quality-inspector"
      className="mt-5 border-t border-slate-800 pt-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">
            {t("qualityInspector.title")}
          </h3>
          <p className="mt-1 text-xs text-slate-500">
            {t("qualityInspector.description")}
          </p>
        </div>
        <button
          type="button"
          data-testid="quality-inspection-run"
          disabled={disabled}
          onClick={runInspection}
          className="shrink-0 rounded border border-slate-600 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t("qualityInspector.runInspection")}
        </button>
      </div>

      {status ? (
        <p role="status" className="mt-3 text-xs text-emerald-300">
          {status}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-3 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      {inspection ? (
        <ul className="mt-3 space-y-2">
          {inspection.findings.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              disabled={disabled || preview !== null}
              onPreview={previewFix}
            />
          ))}
        </ul>
      ) : null}

      {preview ? (
        <div
          data-testid="quality-fix-preview"
          className="mt-3 rounded border border-violet-500/50 bg-violet-950/20 p-3"
        >
          <p className="text-xs font-medium text-violet-200">
            {t("qualityInspector.explicitPreview")}
          </p>
          <code className="mt-1 block break-all text-[10px] text-slate-400">
            {preview.patch.reasonCode} · {preview.patch.path.join(" / ")}
          </code>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="rounded bg-red-950/50 p-2">
              <p className="text-[10px] uppercase text-red-300">
                {t("qualityInspector.before")}
              </p>
              <p className="mt-1 break-all text-red-100">
                {valueLabel(preview.patch.before)}
              </p>
            </div>
            <div className="rounded bg-emerald-950/50 p-2">
              <p className="text-[10px] uppercase text-emerald-300">
                {t("qualityInspector.after")}
              </p>
              <p className="mt-1 break-all text-emerald-100">
                {valueLabel(preview.patch.after)}
              </p>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <button
              type="button"
              data-testid="quality-fix-cancel"
              onClick={() => {
                setPreview(null);
                setStatus(t("qualityInspector.previewCanceled"));
              }}
              className="rounded border border-slate-600 px-2 py-1.5 text-xs"
            >
              {t("qualityInspector.cancel")}
            </button>
            <button
              type="button"
              data-testid="quality-fix-apply"
              disabled={disabled}
              onClick={applyFix}
              className="rounded bg-violet-600 px-2 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t("qualityInspector.applyFix")}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
