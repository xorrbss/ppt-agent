"use client";
import { useRef, useState } from "react";
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
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const epoch = useRef(0);
  const selectFiles = (next: File[]) => {
    if (next.some((file) => !/\.(pptx|pdf|png|jpe?g|webp)$/i.test(file.name))) {
      notify.error("디자인 참고 파일 형식 확인", "PPTX, PDF, PNG, JPEG 또는 WebP 파일을 선택해 주세요."); return;
    }
    epoch.current += 1;
    setFiles(next);
    onConfigChange("svgDesignReferenceSnapshotId", undefined);
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
      notify.error("디자인 참고 등록 실패", "파일을 확인하고 다시 등록해 주세요.");
    } finally { setUploading(false); }
  };
  return <details className="mt-3 rounded-xl border border-slate-200 p-3">
    <summary className="cursor-pointer text-sm font-medium">디자인 참고 파일 (선택)</summary>
    <p className="mt-2 text-xs text-slate-600">색상·서체·분위기 참고에 사용할 파일을 별도로 선택해 주세요. 내용 자료와 별개로 등록됩니다.</p>
    <p className="mt-2 text-xs text-slate-500">PPTX는 색상·서체를 참고하며, 화면 모습까지 참고하려면 PDF나 이미지를 선택해 주세요.</p>
    {disabled ? <div><p className="mt-2 text-xs text-amber-700">등록 디자인 또는 회사 표준 선택을 해제하면 참고 파일을 등록할 수 있습니다.</p>{(files.length > 0 || config.svgDesignReferenceSnapshotId) ? <Button type="button" variant="ghost" size="sm" onClick={() => selectFiles([])}>참고 해제</Button> : null}</div> : <div className="mt-2 space-y-2">
      <SupportingDoc files={files} onFilesChange={selectFiles} accept=".pptx,.pdf,.png,.jpg,.jpeg,.webp" multiple={false} compact />
      <div className="flex items-center gap-2">
        <Button type="button" variant="outline" size="sm" disabled={uploading || files.length !== 1} onClick={() => void register()}>{uploading ? "참고 등록 중…" : "디자인 참고 등록"}</Button>
        {config.svgDesignReferenceSnapshotId ? <span role="status" className="text-xs text-emerald-700">디자인 참고 등록됨</span> : null}
        {(files.length > 0 || config.svgDesignReferenceSnapshotId) ? <Button type="button" variant="ghost" size="sm" onClick={() => selectFiles([])}>참고 해제</Button> : null}
      </div>
    </div>}
  </details>;
}
