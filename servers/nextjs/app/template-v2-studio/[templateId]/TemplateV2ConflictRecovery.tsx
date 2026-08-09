"use client";

import { useTranslations } from "next-intl";

import {
  serializeTemplateV2ConflictRecoveryBundle,
  templateV2ConflictRecoveryFilename,
  type TemplateV2ConflictSnapshot,
} from "@/lib/template-v2-studio-conflict";

export default function TemplateV2ConflictRecovery({
  snapshot,
  onReload,
  onRebase,
  rebasing,
}: {
  snapshot: TemplateV2ConflictSnapshot;
  onReload: () => void;
  onRebase: () => void;
  rebasing: boolean;
}) {
  const t = useTranslations("studio");

  function downloadLocalEdits() {
    const capturedAt = new Date();
    const blob = new Blob(
      [serializeTemplateV2ConflictRecoveryBundle(snapshot, capturedAt)],
      { type: "application/json" }
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = templateV2ConflictRecoveryFilename(
      snapshot.templateId,
      capturedAt
    );
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex shrink-0 items-center gap-2">
      <button
        type="button"
        className="rounded border border-red-300/40 px-3 py-1 disabled:opacity-50"
        onClick={onRebase}
        disabled={rebasing}
        title={t("conflictRecovery.rebaseTitle")}
      >
        {rebasing
          ? t("conflictRecovery.checkingRevision")
          : t("conflictRecovery.rebase")}
      </button>
      <button
        type="button"
        className="rounded border border-red-300/40 px-3 py-1"
        onClick={downloadLocalEdits}
      >
        {t("conflictRecovery.download")}
      </button>
      <button
        type="button"
        className="rounded border border-red-300/40 px-3 py-1"
        onClick={() => {
          if (window.confirm(t("conflictRecovery.reloadConfirm"))) {
            onReload();
          }
        }}
      >
        {t("conflictRecovery.reload")}
      </button>
    </div>
  );
}
