"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

type DraftPage = {
  index: number; author: string; render: string; persist: string;
  quality: string; imageAvailability: "unconfirmed"; pageVersion: string;
};
type DraftPreview = {
  kind: "checkpoint_draft"; published: false; jobId: string;
  operationState: string; jobState: string; stage: string | null;
  totalPages: number; stopCode: string | null; pages: DraftPage[];
};
const TERMINAL = new Set(["failed", "dead_letter", "cancelled", "deadline_exceeded", "rejected", "expired"]);
const STAGES: Record<string, string> = {
  author: "SVG 작성", render: "렌더링", quality: "품질 검토", vqa: "시각 품질 검토", persist: "게시",
};
const QUALITY: Record<string, string> = {
  verified: "검증됨", review_pending: "검토 대기", failed: "실패", passed: "통과", blocked: "차단", draft: "초안", unconfirmed: "미확인",
};

/** Separate from published slides: one selected image, no deck asset preloading. */
export default function DraftCheckpointPreview({ presentationId }: { presentationId: string }) {
  const [draft, setDraft] = useState<DraftPreview | null>(null);
  const [selected, setSelected] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loadFailed, setLoadFailed] = useState(false);
  const [failedPageKey, setFailedPageKey] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoadFailed(false);
    void fetch(`/api/v1/ppt/presentation/${encodeURIComponent(presentationId)}/draft-preview`, {
      credentials: "same-origin", cache: "no-store", signal: controller.signal,
    }).then(async (response) => {
      if (response.status === 404) return null;
      if (!response.ok) throw new Error("draft_preview_unavailable");
      return response.json() as Promise<DraftPreview>;
    }).then((result) => {
      if (controller.signal.aborted) return;
      if (result === null) { setDraft(null); return; }
      if (result?.kind !== "checkpoint_draft" || result.published !== false || !Array.isArray(result.pages)) return;
      setDraft(result);
      setSelected((previous) => result.pages.some((page) => page.index === previous)
        ? previous : result.pages[0]?.index ?? 0);
    }).catch(() => { if (!controller.signal.aborted) setLoadFailed(true); });
    return () => controller.abort();
  }, [presentationId, refresh]);

  useEffect(() => {
    if (!draft || TERMINAL.has(draft.operationState) || TERMINAL.has(draft.jobState) || draft.jobState === "succeeded") return;
    const timer = window.setTimeout(() => setRefresh((value) => value + 1), 10_000);
    return () => window.clearTimeout(timer);
  }, [draft]);

  if (!draft || draft.pages.length === 0) {
    return loadFailed ? <div className="p-4 text-sm text-slate-700" role="alert">
      초안 미리보기를 불러오지 못했습니다.
      <Button variant="outline" size="sm" onClick={() => setRefresh((value) => value + 1)}>다시 불러오기</Button>
    </div> : null;
  }
  const page = draft.pages.find((item) => item.index === selected);
  const pageKey = `${draft.jobId}:${selected}`;
  const stopped = TERMINAL.has(draft.operationState) || TERMINAL.has(draft.jobState);
  const count = (stage: "author" | "render" | "persist") => draft.pages.filter((item) => item[stage] === "completed").length;
  const imageUrl = `/api/v1/ppt/presentation/${encodeURIComponent(presentationId)}/draft-preview/${selected}/image?job_id=${encodeURIComponent(draft.jobId)}&page_version=${encodeURIComponent(page?.pageVersion ?? "")}`;
  const quota = draft.stopCode?.includes("quota") || draft.stopCode?.includes("budget")
    || draft.stopCode === "retry_amplification_ceiling_exceeded";
  return <section aria-label="생성 초안 미리보기" className="min-w-0 flex-1 overflow-y-auto border-r border-slate-200 bg-slate-50 p-4">
    <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-slate-800">
      <h2 className="font-semibold">생성 초안 · 게시 전</h2>
      <p className="mt-1">저장된 렌더 결과입니다. 전체 품질 검토와 최종 게시가 완료되지 않았습니다.</p>
      <p className="mt-1">각 페이지의 저장된 이미지 유무는 선택할 때 확인합니다.</p>
      <p className="mt-2" aria-live="polite">SVG 작성 {count("author")}/{draft.totalPages} · 렌더링 {count("render")}/{draft.totalPages} · 게시 {count("persist")}/{draft.totalPages}</p>
      <p className="mt-1">{stopped ? "작업 중단" : draft.jobState === "succeeded" ? "작업 완료 · 게시 여부 확인 필요" : "진행 중"}{draft.stage ? ` · 저장된 진행 단계: ${STAGES[draft.stage] ?? "처리 중"}` : ""}</p>
      {stopped && <p className="mt-1">{quota ? "사용 한도에 도달해 후속 작업이 중단되었습니다." : "생성이 중단되었습니다. 자세한 사유는 작업 내역에서 확인할 수 있습니다."}</p>}
      {loadFailed && <p role="alert" className="mt-1">상태를 새로 불러오지 못해 마지막 조회 결과를 표시합니다.</p>}
    </div>
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <label className="text-sm" htmlFor="draft-preview-page">페이지</label>
      <select id="draft-preview-page" value={selected} className="rounded border border-slate-300 bg-white p-2 text-sm"
        onChange={(event) => { setSelected(Number(event.target.value)); setFailedPageKey(null); }}>
        {draft.pages.map((item) => <option key={item.index} value={item.index}>
          {item.index + 1} / {draft.totalPages} · 이미지 미확인
        </option>)}
      </select>
      <Button variant="outline" size="sm" onClick={() => setRefresh((value) => value + 1)}>상태 새로고침</Button>
      <span className="text-xs text-slate-600">이 페이지 품질: {QUALITY[page?.quality ?? "unconfirmed"] ?? "미확인"}</span>
    </div>
    {failedPageKey === pageKey ? <div role="alert" className="p-4 text-sm text-red-700">
      <p>이 페이지의 저장된 이미지를 불러오지 못했습니다.</p>
      <Button variant="outline" size="sm" onClick={() => setFailedPageKey(null)}>이미지 다시 불러오기</Button>
    </div>
      : <img key={imageUrl} src={imageUrl} alt={`${selected + 1}페이지 생성 초안`} loading="eager" onError={() => setFailedPageKey(pageKey)} className="h-auto w-full bg-white shadow-sm" />}
  </section>;
}
