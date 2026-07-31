"use client";

import type { TemplateV2StudioJournalEntry } from "@/lib/template-v2-studio-journal";

export default function TemplateV2DraftRecovery({
  draft,
  serverRevision,
  safeToRestore,
  onRestore,
  onDiscard,
}: {
  draft: TemplateV2StudioJournalEntry;
  serverRevision: number;
  safeToRestore: boolean;
  onRestore: () => void;
  onDiscard: () => void;
}) {
  const capturedAt = new Date(draft.capturedAt);
  const capturedLabel = Number.isNaN(capturedAt.getTime())
    ? draft.capturedAt
    : capturedAt.toLocaleString();

  return (
    <section
      aria-label="Recovered browser draft"
      className="mx-6 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-400/40 bg-amber-950/30 px-4 py-3 text-sm text-amber-100"
    >
      <div>
        <p className="font-semibold">Unsaved browser draft found</p>
        <p className="mt-1 text-amber-200">
          Captured {capturedLabel} from revision {draft.baseRevision}. The
          server is now at revision {serverRevision}.
        </p>
        {!safeToRestore ? (
          <p className="mt-1 text-red-200">
            Server layouts changed too, so automatic recovery is blocked to
            prevent an overwrite. Download the draft before discarding it.
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          className="rounded border border-amber-300/50 px-3 py-1.5 font-medium"
          onClick={onRestore}
          disabled={!safeToRestore}
        >
          Restore draft
        </button>
        <button
          type="button"
          className="rounded border border-amber-300/30 px-3 py-1.5"
          onClick={() => {
            const blob = new Blob([`${JSON.stringify(draft, null, 2)}\n`], {
              type: "application/json",
            });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = `${draft.templateId.replace(
              /[^a-zA-Z0-9_-]+/g,
              "-"
            )}-browser-draft.json`;
            document.body.append(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(url);
          }}
        >
          Download draft
        </button>
        <button
          type="button"
          className="rounded border border-amber-300/30 px-3 py-1.5"
          onClick={() => {
            if (
              window.confirm(
                "Discard this browser draft? This cannot be undone."
              )
            ) {
              onDiscard();
            }
          }}
        >
          Discard draft
        </button>
      </div>
    </section>
  );
}
