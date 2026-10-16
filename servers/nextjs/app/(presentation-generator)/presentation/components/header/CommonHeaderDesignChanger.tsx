"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { isRecord } from "@/lib/guards";
import { POLL_INTERVAL_MS } from "@/lib/timeouts";
import { getWorkspaceTimeline, postWorkspaceCommand, WorkspaceApiError } from "../../../services/api/workspace";
import type { WorkspaceOperationAnchor } from "../../workspace/types";

function messageFor(error: unknown): string {
  if (error instanceof WorkspaceApiError && ["revision_conflict", "workspace_expected_revision_required"].includes(error.code ?? "")) {
    return "자료가 변경되었습니다. 최신 자료를 불러온 뒤 다시 요청해 주세요.";
  }
  if (error instanceof WorkspaceApiError && error.code === "svg_common_header_design_reference_admission_required") return "현재 자료에 등록된 디자인 참고 자료만 사용할 수 있습니다.";
  if (error instanceof WorkspaceApiError && error.code === "svg_common_header_terminal_retry_unsupported") return "완료된 상단 수정 작업의 재시도는 지원하지 않습니다. 기존 결과와 비용 기록은 보존됩니다.";
  if (error instanceof Error && !/^[a-z][a-z0-9_:.]*$/.test(error.message) && !error.message.startsWith("Workspace request failed")) return error.message;
  return "상단 디자인 변경 상태를 확인할 수 없습니다. 작업 내역을 확인해 주세요.";
}

export function fullPageHeaderLocked(presentation: unknown): boolean {
  if (!isRecord(presentation)) return false;
  const theme = isRecord(presentation.theme) ? presentation.theme : {};
  const svg = isRecord(theme.svg) ? theme.svg : {};
  if ([presentation.source_mode, svg.sourceMode, svg.source_mode].some((mode) => mode === "full_page_preserve" || mode === "source_preserve")) return true;
  return Array.isArray(presentation.slides) && presentation.slides.some((slide: unknown) => {
    if (!isRecord(slide)) return false;
    const content = isRecord(slide.content) ? slide.content : {};
    const receipt = isRecord(content.svgReceipt) ? content.svgReceipt : {};
    const source = isRecord(receipt.source_preservation) ? receipt.source_preservation : {};
    const rasterPin = isRecord(receipt.rasterApprovalPin) ? receipt.rasterApprovalPin : {};
    if (Array.isArray(rasterPin.full_page_preserve_pages) && rasterPin.full_page_preserve_pages.includes(receipt.rasterSourcePageNumber)) return true;
    return [slide.render_mode, slide.renderMode, content.render_mode, content.renderMode, receipt.render_mode, source.render_mode].includes("full_page_preserve");
  });
}

export function existingHeaderDesignReferences(presentation: unknown): string[] {
  if (!isRecord(presentation) || !isRecord(presentation.theme)) return [];
  const svg = presentation.theme.svg;
  if (!isRecord(svg) || !isRecord(svg.styleSnapshot)) return [];
  const template = svg.styleSnapshot.design_pack_template;
  if (!isRecord(template) || !isRecord(template.provenance)) return [];
  const snapshotId = template.provenance.snapshot_id;
  return typeof snapshotId === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(snapshotId) ? [snapshotId] : [];
}

export function existingHeaderDocumentValues(presentation: unknown): Record<string, string> {
  if (!isRecord(presentation)) return {};
  const theme = isRecord(presentation.theme) ? presentation.theme : {};
  const design = isRecord(theme.commonHeaderDesign) ? theme.commonHeaderDesign : {};
  if (isRecord(design.document_values)) {
    return Object.fromEntries(Object.entries(design.document_values).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
  }
  const outlines = isRecord(presentation.outlines) ? presentation.outlines.slides : undefined;
  if (Array.isArray(outlines)) {
    const values: Record<string, string> = {};
    let hasCurrentBinding = false;
    for (const slide of outlines) {
      if (!isRecord(slide) || !isRecord(slide.common_header) || !Array.isArray(slide.common_header.fields)) continue;
      hasCurrentBinding = true;
      for (const field of slide.common_header.fields) {
        if (isRecord(field) && field.user_fixed === true && typeof field.value_ref === "string" && typeof field.value === "string" && typeof field.source_ref === "string" && field.source_ref.includes("#/common_header_design/document_values/")) values[field.value_ref] = field.value;
      }
    }
    if (hasCurrentBinding) return values;
  }
  return {};
}

export default function CommonHeaderDesignChanger({ presentationId, expectedRevision, disabled, unavailableReason, referenceSnapshotIds = [], documentValues = {}, onSaved }: {
  presentationId: string;
  expectedRevision?: number;
  disabled?: boolean;
  unavailableReason?: string;
  referenceSnapshotIds?: string[];
  documentValues?: Record<string, string>;
  onSaved?: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [documentLabel, setDocumentLabel] = useState(documentValues.document_label ?? "");
  const [documentLabelChanged, setDocumentLabelChanged] = useState(false);
  const [useReference, setUseReference] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);
  const retryKeys = useRef(new Map<string, string>());
  const busy = useRef(false);
  const saved = useRef(onSaved);
  saved.current = onSaved;

  useEffect(() => {
    if (!operationId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const inspect = async () => {
      try {
        const timeline = await getWorkspaceTimeline(presentationId, { limit: 100, signal: controller.signal });
        const operation = timeline.items.find((item): item is WorkspaceOperationAnchor => item.anchorType === "operation" && item.operationId === operationId);
        if (operation && ["completed", "succeeded"].includes(operation.status)) {
          await saved.current?.();
          if (controller.signal.aborted) return;
          setPending(false); setOperationId(null); setOpen(false); busy.current = false;
          return;
        }
        if (operation && ["failed", "cancelled", "rejected", "blocked", "dead_letter"].includes(operation.status)) {
          setError(operation.error ? messageFor(new Error(operation.error)) : "상단 디자인 변경을 완료하지 못했습니다. 작업 내역을 확인해 주세요.");
          setPending(false); setOperationId(null); busy.current = false;
          return;
        }
      } catch (failure) {
        if (controller.signal.aborted) return;
        setError(messageFor(failure));
      }
      if (!controller.signal.aborted) timer = setTimeout(() => void inspect(), POLL_INTERVAL_MS);
    };
    void inspect();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [operationId, presentationId]);

  useEffect(() => {
    if (!open && !pending) {
      setDocumentLabel(documentValues.document_label ?? "");
      setDocumentLabelChanged(false);
    }
  }, [open, pending, documentValues.document_label, expectedRevision]);

  const submit = async () => {
    if (busy.current || disabled || expectedRevision === undefined) return;
    const nextDocumentValues = { ...documentValues };
    if (documentLabel.trim()) nextDocumentValues.document_label = documentLabel;
    else delete nextDocumentValues.document_label;
    const design = {
      ...(documentLabelChanged ? { document_values: nextDocumentValues } : {}),
      ...(instructions.trim() ? { instructions: instructions.trim() } : {}),
      ...(referenceSnapshotIds.length ? { reference_snapshot_ids: useReference ? referenceSnapshotIds : [] } : {}),
    };
    const context = { target: "common_header", common_header_design: design };
    const signature = JSON.stringify({ presentationId, expectedRevision, context });
    const key = retryKeys.current.get(signature) ?? crypto.randomUUID();
    retryKeys.current.set(signature, key);
    busy.current = true; setPending(true); setError(null);
    try {
      const receipt = await postWorkspaceCommand(presentationId, {
        command: instructions.trim() || "공통 상단 디자인을 새로 구성해 주세요.",
        commandType: "design.apply", context, expectedRevision, idempotencyKey: key, assetIds: [],
      });
      if (!receipt.operationId) throw new Error("상단 디자인 작업 접수를 확인할 수 없습니다. 작업 내역을 확인해 주세요.");
      setOperationId(receipt.operationId);
    } catch (failure) {
      setError(messageFor(failure)); setPending(false); busy.current = false;
    }
  };

  return <Dialog open={open} onOpenChange={setOpen}>
    <span title={unavailableReason}><DialogTrigger asChild><Button type="button" variant="outline" size="sm" disabled={disabled || expectedRevision === undefined || pending}>상단 디자인 수정</Button></DialogTrigger></span>
    <DialogContent>
      <DialogHeader><DialogTitle>상단 디자인 수정</DialogTitle><DialogDescription>원하는 분위기나 브랜드 표현을 자유롭게 적어 주세요. LLM이 완성 슬라이드 두 안을 비교해 자동으로 선택합니다.</DialogDescription></DialogHeader>
      <label className="text-sm">상단 디자인 설명 (선택)<textarea aria-label="상단 디자인 설명" className="mt-2 w-full rounded border p-3" rows={4} value={instructions} onChange={(event) => setInstructions(event.target.value)} disabled={pending} /></label>
      <label className="text-sm">상단 보조 문구 (선택)<input aria-label="상단 보조 문구" className="mt-2 w-full rounded border p-3" value={documentLabel} onChange={(event) => { setDocumentLabel(event.target.value); setDocumentLabelChanged(true); }} disabled={pending} placeholder="문서명이나 상단에 표시할 고정 문구" /></label>
      {referenceSnapshotIds.length > 0 ? <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={useReference} onChange={(event) => setUseReference(event.target.checked)} disabled={pending} />기존에 등록한 디자인 참고 자료 사용</label> : null}
      {pending ? <p role="status" className="text-sm text-slate-600">상단 디자인을 수정하고 있습니다. 작업 내역에서도 진행 상황을 확인할 수 있습니다.</p> : null}
      {error ? <p role="alert" className="text-sm text-red-600">{error}</p> : null}
      <DialogFooter><Button type="button" variant="ghost" onClick={() => setOpen(false)}>닫기</Button><Button type="button" disabled={disabled || pending || expectedRevision === undefined} onClick={() => void submit()}>{pending ? "수정 중…" : "상단 수정"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
