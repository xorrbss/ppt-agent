"use client";

import { useTranslations } from "next-intl";

import type {
  MasterRevisionEnvelope,
  MasterValidation,
  VisualDirection,
} from "@/lib/authored-vnext/master/contracts";

interface MasterSetupPublishPanelProps {
  applyLabel?: string;
  busy: boolean;
  error: string | null;
  onApply?: (
    published: MasterRevisionEnvelope,
    visualDirection: VisualDirection,
  ) => void;
  onPublish: () => void;
  onSave: () => void;
  published: MasterRevisionEnvelope | null;
  publishedStale: boolean;
  validation: MasterValidation;
  visualDirection: VisualDirection;
}

/** A compact entry point: publish owns the save -> strict validate -> publish flow. */
export default function MasterSetupPublishPanel({
  applyLabel,
  busy,
  error,
  onApply,
  onPublish,
  onSave,
  published,
  publishedStale,
  validation,
  visualDirection,
}: MasterSetupPublishPanelProps) {
  const t = useTranslations("authoredVnext.master");
  const issues = validation.errors.slice(0, 2);
  const saveLabel = t("publish.save");
  const publishLabel = t("publish.publishAction");

  return (
    <section
      aria-label={`${saveLabel} / ${publishLabel}`}
      aria-live="polite"
      className="w-full space-y-2 xl:w-[320px]"
    >
      <div className="flex gap-2">
        <button
          aria-label={saveLabel}
          className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm font-bold text-slate-800 disabled:opacity-40"
          data-testid="authored-master-save"
          disabled={busy || !validation.valid}
          onClick={onSave}
          type="button"
        >
          {saveLabel}
        </button>
        <button
          aria-label={publishLabel}
          className="flex-1 rounded bg-blue-700 px-3 py-2 text-sm font-bold text-white disabled:opacity-40"
          data-testid="authored-master-publish"
          disabled={
            busy ||
            !validation.valid ||
            Boolean(published && !publishedStale)
          }
          onClick={onPublish}
          type="button"
        >
          {busy ? t("publish.busy") : publishLabel}
        </button>
      </div>
      {!validation.valid && issues.length > 0 && (
        <div
          className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-950"
          role="status"
        >
          {issues.map((issue) => (
            <p key={`${issue.code}:${issue.path}`}>
              {t("validation.issue", {
                code: issue.code,
                message: issue.message,
              })}
            </p>
          ))}
        </div>
      )}
      {error && (
        <p className="rounded bg-red-50 p-2 text-xs text-red-900" role="alert">
          {t("publish.error", { code: error })}
        </p>
      )}
      {published && !publishedStale && (
        <div
          className="space-y-2 rounded bg-emerald-50 p-2 text-xs text-emerald-950"
          role="status"
        >
          <p>
            {t("phase.published")} · r{published.revision}
          </p>
          {onApply && applyLabel && (
            <button
              className="w-full rounded bg-violet-700 px-3 py-2 text-sm font-bold text-white"
              data-testid="apply-after-publish"
              onClick={() => onApply(published, visualDirection)}
              type="button"
            >
              {applyLabel}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
