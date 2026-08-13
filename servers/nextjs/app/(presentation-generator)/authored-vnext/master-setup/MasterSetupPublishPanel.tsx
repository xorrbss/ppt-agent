"use client";

import { useTranslations } from "next-intl";

import type {
  MasterRevisionEnvelope,
  MasterValidation,
  VisualDirection,
} from "@/lib/authored-vnext/master/contracts";
import type { MasterSetupPhase } from "@/lib/authored-vnext/master/view-model";

interface MasterSetupPublishPanelProps {
  busy: boolean;
  continueHref?: string;
  draft: MasterRevisionEnvelope | null;
  draftStale: boolean;
  error: string | null;
  onCreateDraft: () => void;
  onPreview: () => void;
  onPublished?: (
    published: MasterRevisionEnvelope,
    visualDirection: VisualDirection,
  ) => void;
  onPublish: () => void;
  onValidate: () => void;
  phase: MasterSetupPhase;
  previewReady: boolean;
  published: MasterRevisionEnvelope | null;
  publishedStale: boolean;
  remotelyValidated: boolean;
  validation: MasterValidation;
  visualDirection: VisualDirection;
}

export default function MasterSetupPublishPanel({
  busy,
  continueHref,
  draft,
  draftStale,
  error,
  onCreateDraft,
  onPreview,
  onPublished,
  onPublish,
  onValidate,
  phase,
  previewReady,
  published,
  publishedStale,
  remotelyValidated,
  validation,
  visualDirection,
}: MasterSetupPublishPanelProps) {
  const t = useTranslations("authoredVnext.master");

  return (
    <aside
      aria-live="polite"
      className="h-fit space-y-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
    >
      <h2 className="text-lg font-bold">{t("publish.title")}</h2>
      <p className="text-xs text-slate-600">{t("publish.description")}</p>
      <p className="rounded bg-slate-100 p-3 text-sm">{t(`phase.${phase}`)}</p>
      <button
        className="w-full rounded bg-slate-950 px-4 py-3 text-sm font-bold text-white disabled:opacity-40"
        disabled={busy || !validation.valid}
        data-testid="authored-master-create-draft"
        onClick={onCreateDraft}
        type="button"
      >
        {t("publish.createDraft")}
      </button>
      <button
        className="w-full rounded border px-4 py-3 text-sm font-bold disabled:opacity-40"
        disabled={busy || !draft || draftStale}
        data-testid="authored-master-validate"
        onClick={onValidate}
        type="button"
      >
        {t("publish.validate")}
      </button>
      <button
        className="w-full rounded bg-blue-700 px-4 py-3 text-sm font-bold text-white disabled:opacity-40"
        disabled={busy || !remotelyValidated}
        data-testid="authored-master-publish"
        onClick={onPublish}
        type="button"
      >
        {t("publish.publish")}
      </button>
      <button
        className="w-full rounded bg-emerald-700 px-4 py-3 text-sm font-bold text-white disabled:opacity-40"
        disabled={busy || !published || publishedStale}
        data-testid="authored-master-preview"
        onClick={onPreview}
        type="button"
      >
        {t("publish.preview")}
      </button>
      {error && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-900" role="alert">
          {t("publish.error", { code: error })}
        </p>
      )}
      {previewReady && (
        <p className="rounded bg-emerald-50 p-3 text-xs">
          {t("publish.previewReady")}
        </p>
      )}
      {published && !publishedStale && continueHref && (
        <a
          className="block rounded bg-violet-700 px-4 py-3 text-center text-sm font-bold text-white"
          data-testid="continue-after-publish"
          href={continueHref}
          onClick={(event) => {
            if (!onPublished) return;
            event.preventDefault();
            onPublished(published, visualDirection);
          }}
        >
          {t("publish.continue")}
        </a>
      )}
    </aside>
  );
}
