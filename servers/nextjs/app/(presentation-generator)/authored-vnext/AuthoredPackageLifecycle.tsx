"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { authoredVnextPackageClient, type OoxmlPackageVersion, type OoxmlReadBack } from "@/lib/authored-vnext/flow/package-client";

interface Props {
  masterContract: Record<string, unknown>;
}

function operationKey(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

export default function AuthoredPackageLifecycle({ masterContract }: Props) {
  const t = useTranslations("authoredVnext.package");
  const [source, setSource] = useState<OoxmlPackageVersion | null>(null);
  const [editable, setEditable] = useState<OoxmlPackageVersion | null>(null);
  const [readBack, setReadBack] = useState<OoxmlReadBack | null>(null);
  const [expectedText, setExpectedText] = useState("");
  const [replacementText, setReplacementText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const execute = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await work(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "authored_vnext_ooxml_request_failed"); }
    finally { setBusy(false); }
  };

  const refresh = async (packageId: string) => {
    const [current, receipt] = await Promise.all([
      authoredVnextPackageClient.inspect(packageId),
      authoredVnextPackageClient.readBack(packageId),
    ]);
    setEditable(current);
    setReadBack(receipt);
  };

  return <section className="fixed right-4 top-4 z-[75] max-h-[calc(100vh-2rem)] w-[26rem] overflow-y-auto rounded-xl border border-slate-200 bg-white p-3 text-xs shadow-xl" data-testid="authored-package-lifecycle">
    <h2 className="font-bold">{t("title")}</h2>
    <input accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation" aria-label={t("fileAria")} className="mt-2 block w-full" disabled={busy} onChange={(event) => {
      const file = event.currentTarget.files?.[0];
      if (!file) return;
      void execute(async () => {
        const imported = await authoredVnextPackageClient.importPptx(file, operationKey("import"));
        setSource(imported.package);
        setEditable(null);
        setReadBack(null);
      });
    }} type="file" />
    {source && <div className="mt-2 space-y-2">
      <p data-slide-count={source.inspection.slideCount} data-testid="package-import-status">{t("imported", { slides: source.inspection.slideCount, state: source.state })}</p>
      {source.inspection.quarantineReasons.length > 0 && <p className="text-red-700">{t("quarantined", { reasons: source.inspection.quarantineReasons.join(", ") })}</p>}
      <div className="flex flex-wrap gap-1">
        <button data-testid="authored-package-preserve" disabled={busy || source.state === "quarantined"} onClick={() => void execute(async () => {
          const fork = await authoredVnextPackageClient.fork(source, "PRESERVE_EXACT", operationKey("preserve"));
          setEditable(fork.package);
          await refresh(fork.package.packageId);
        })} type="button">{t("preserveFork")}</button>
        <button data-testid="authored-package-normalize" disabled={busy || source.state === "quarantined"} onClick={() => void execute(async () => {
          const fork = await authoredVnextPackageClient.fork(source, "CORPORATE_NORMALIZE", operationKey("normalize"), masterContract);
          setEditable(fork.package);
          await refresh(fork.package.packageId);
        })} type="button">{t("normalizeFork")}</button>
      </div>
    </div>}
    {editable && <div className="mt-3 space-y-2 border-t pt-2">
      <p
        data-mode={editable.mode}
        data-revision={editable.revision}
        data-slide-count={editable.inspection.slideCount}
        data-testid="package-editable-status"
      >
        {t("editable", { mode: editable.mode, revision: editable.revision })}
      </p>
      <div className="flex flex-wrap gap-1">
        <button data-testid="authored-package-noop" disabled={busy} onClick={() => void execute(async () => {
          const completed = await authoredVnextPackageClient.operate(editable, "NOOP_SAVE", operationKey("noop"));
          if (completed.state !== "succeeded") throw new Error(completed.errorCode ?? "authored_vnext_ooxml_operation_failed");
          await refresh(editable.packageId);
        })} type="button">{t("noopSave")}</button>
        <button data-testid="authored-package-save-export" disabled={busy} onClick={() => void execute(async () => {
          const completed = await authoredVnextPackageClient.operate(editable, "SAVE_EXPORT", operationKey("save-export"));
          if (completed.state !== "succeeded") throw new Error(completed.errorCode ?? "authored_vnext_ooxml_operation_failed");
          await refresh(editable.packageId);
        })} type="button">{t("saveExport")}</button>
        <button data-testid="authored-package-rollback" disabled={busy || editable.revision <= 1} onClick={() => void execute(async () => {
          const restored = await authoredVnextPackageClient.rollback(editable, 1, operationKey("rollback"));
          await refresh(restored.packageId);
        })} type="button">{t("rollback")}</button>
        <a className="underline" href={authoredVnextPackageClient.artifactUrl(editable.packageId)}>{t("download")}</a>
      </div>
      {editable.mode === "PRESERVE_EXACT" && <div className="grid grid-cols-2 gap-1">
        <input aria-label={t("expectedTextAria")} data-testid="authored-package-expected-text" onChange={(event) => setExpectedText(event.target.value)} placeholder={t("expectedText")} value={expectedText} />
        <input aria-label={t("replacementTextAria")} data-testid="authored-package-replacement-text" onChange={(event) => setReplacementText(event.target.value)} placeholder={t("replacementText")} value={replacementText} />
        <button className="col-span-2" data-testid="authored-package-targeted-edit" disabled={busy || !expectedText || !replacementText} onClick={() => void execute(async () => {
          const completed = await authoredVnextPackageClient.operate(editable, "PATCH_TEXT_SLICE", operationKey("patch"), {
            partName: "ppt/slides/slide1.xml",
            selector: { kind: "placeholder", role: "title", occurrence: 0 },
            expectedText,
            replacementText,
          });
          if (completed.state !== "succeeded") throw new Error(completed.errorCode ?? "authored_vnext_ooxml_operation_failed");
          await refresh(editable.packageId);
        })} type="button">{t("targetedEdit")}</button>
      </div>}
    {readBack && <p data-preserved={String(readBack.untouchedPartsVerified)} data-revision={readBack.package.revision} data-silent-loss={readBack.silentLossCount} data-testid="package-readback-status">{t("readBack", { revision: readBack.package.revision, preserved: String(readBack.untouchedPartsVerified), loss: readBack.silentLossCount })}</p>}
    </div>}
    {busy && <p aria-live="polite">{t("busy")}</p>}
    {error && <p className="text-red-700" role="alert">{t("error", { code: error })}</p>}
  </section>;
}
