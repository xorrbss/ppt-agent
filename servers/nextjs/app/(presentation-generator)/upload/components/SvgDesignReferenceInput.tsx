"use client";
import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { notify } from "@/components/ui/sonner";
import SupportingDoc from "./SupportingDoc";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import type { PresentationConfig } from "../type";

export default function SvgDesignReferenceInput({ config, onConfigChange, disabled = false }: {
  config: PresentationConfig;
  onConfigChange: (key: keyof PresentationConfig, value: unknown) => void;
  disabled?: boolean;
}) {
  const t = useTranslations("uploadExtras");
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const epoch = useRef(0);
  const selectFiles = (next: File[]) => {
    if (next.some((file) => !/\.(pptx|pdf|png|jpe?g|webp)$/i.test(file.name))) {
      notify.error(t("svgReferenceInvalidTypeTitle"), t("svgReferenceInvalidTypeBody")); return;
    }
    epoch.current += 1;
    setFiles(next);
    onConfigChange("svgDesignReferenceSnapshotId", undefined);
    onConfigChange("commonHeaderDesign", { ...config.commonHeaderDesign, reference_snapshot_ids: undefined });
    onConfigChange("svgDesignReferencePending", next.length > 0);
  };
  const register = async () => {
    if (disabled || uploading || files.length !== 1) return;
    const activeEpoch = epoch.current;
    setUploading(true);
    try {
      const admission = await PresentationGenerationApi.uploadDoc(files, `svg-design-reference:${crypto.randomUUID()}`);
      if (activeEpoch !== epoch.current) {
        await PresentationGenerationApi.cleanupDocumentAdmission(admission);
        return;
      }
      onConfigChange("svgDesignReferenceSnapshotId", admission.referenceSnapshotId);
      onConfigChange("svgDesignReferencePending", false);
    } catch {
      notify.error(t("svgReferenceUploadFailedTitle"), t("svgReferenceUploadFailedBody"));
    } finally { setUploading(false); }
  };
  return <details className="mt-3 rounded-xl border border-slate-200 p-3">
    <summary className="cursor-pointer text-sm font-medium">{t("svgReferenceLabel")}</summary>
    <p className="mt-2 text-xs text-slate-600">{t("svgReferenceDescription")}</p>
    <p className="mt-2 text-xs text-slate-500">{t("svgReferencePptxNote")}</p>
    {disabled ? <div><p className="mt-2 text-xs text-amber-700">{t("svgReferenceDisabledNote")}</p>{(files.length > 0 || config.svgDesignReferenceSnapshotId) ? <Button type="button" variant="ghost" size="sm" onClick={() => selectFiles([])}>{t("svgReferenceClear")}</Button> : null}</div> : <div className="mt-2 space-y-2">
      <SupportingDoc files={files} onFilesChange={selectFiles} accept=".pptx,.pdf,.png,.jpg,.jpeg,.webp" multiple={false} compact />
      <div className="flex items-center gap-2">
        <Button type="button" variant="outline" size="sm" disabled={uploading || files.length !== 1} onClick={() => void register()}>{uploading ? t("svgReferenceUploading") : t("svgReferenceUpload")}</Button>
        {config.svgDesignReferenceSnapshotId ? <span role="status" className="text-xs text-emerald-700">{t("svgReferenceRegistered")}</span> : null}
        {(files.length > 0 || config.svgDesignReferenceSnapshotId) ? <Button type="button" variant="ghost" size="sm" onClick={() => selectFiles([])}>{t("svgReferenceClear")}</Button> : null}
      </div>
    </div>}
  </details>;
}
