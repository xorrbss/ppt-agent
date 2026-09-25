"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { DashboardApi } from "../../services/api/dashboard";
import { stagePreviousAttemptPromptEdit } from "../../upload/components/upload-page/usePreviousGenerationAttempt";

const RESTARTABLE_GENERATION_STATUSES = new Set([
  "failed",
  "cancelled",
  "dead_letter",
  "deadline_exceeded",
]);

export function WorkspaceAttemptNavigation({
  presentationId,
  previousPresentationId,
  nextPresentationIds,
  generationStatus,
  restartBlockedReason,
  showHistory = true,
  showRestart = true,
}: {
  presentationId: string;
  previousPresentationId?: string | null;
  nextPresentationIds?: string[] | null;
  generationStatus?: string | null;
  restartBlockedReason?: string | null;
  showHistory?: boolean;
  showRestart?: boolean;
}) {
  const router = useRouter();
  const [editor, setEditor] = useState<{
    prompt: string;
    attachmentNames: string[];
  } | null>(null);
  const [loadingEditor, setLoadingEditor] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [continuing, setContinuing] = useState(false);
  const editorRequestRef = useRef(0);
  const nextIds = showHistory ? nextPresentationIds ?? [] : [];
  const restartAvailable =
    showRestart && generationStatus !== null &&
    generationStatus !== undefined &&
    RESTARTABLE_GENERATION_STATUSES.has(generationStatus);

  useEffect(() => {
    editorRequestRef.current += 1;
    setEditor(null);
    setEditorError(null);
    setLoadingEditor(false);
    setContinuing(false);
  }, [presentationId]);

  useEffect(() => {
    if (restartAvailable) return;
    editorRequestRef.current += 1;
    setEditor(null);
    setLoadingEditor(false);
    setContinuing(false);
  }, [restartAvailable]);

  const openEditor = async () => {
    if (restartBlockedReason || loadingEditor || continuing) return;
    const requestId = editorRequestRef.current + 1;
    editorRequestRef.current = requestId;
    setEditorError(null);
    setLoadingEditor(true);
    try {
      const info = await DashboardApi.getPresentationGenerationInfo(
        presentationId,
      );
      if (requestId !== editorRequestRef.current) return;
      if (!info.can_start_new_attempt) {
        setEditorError(
          "이전 작업이 종료되었는지 확인한 뒤 요청을 수정할 수 있습니다.",
        );
        return;
      }
      setEditor({
        prompt: info.prompt,
        attachmentNames: info.attachment_file_names,
      });
    } catch {
      if (requestId !== editorRequestRef.current) return;
      setEditorError(
        "이전 요청을 불러오지 못했습니다. 접근 권한과 연결 상태를 확인해 주세요.",
      );
    } finally {
      if (requestId === editorRequestRef.current) setLoadingEditor(false);
    }
  };

  const continueToSettings = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !editor ||
      continuing ||
      !restartAvailable ||
      Boolean(restartBlockedReason)
    )
      return;
    setEditorError(null);
    if (!stagePreviousAttemptPromptEdit(presentationId, editor.prompt)) {
      setEditorError(
        "수정 내용을 브라우저에 보관하지 못했습니다. 다시 시도해 주세요.",
      );
      return;
    }
    setContinuing(true);
    router.push(`/upload?previous=${encodeURIComponent(presentationId)}`);
  };

  if (!(showHistory && previousPresentationId) && nextIds.length === 0 && !restartAvailable)
    return null;

  return (
    <nav
      className="flex flex-wrap items-center gap-2 bg-white py-2 text-xs"
      aria-label="생성 시도 이동"
    >
      {showHistory && previousPresentationId && (
        <Link
          href={`/presentation?id=${encodeURIComponent(previousPresentationId)}`}
          className="rounded-full border border-slate-200 px-2.5 py-1 font-medium text-slate-600 hover:bg-slate-50"
        >
          이전 시도
        </Link>
      )}
      {nextIds.length === 1 && (
        <Link
          href={`/presentation?id=${encodeURIComponent(nextIds[0]!)}`}
          className="rounded-full border border-slate-200 px-2.5 py-1 font-medium text-slate-600 hover:bg-slate-50"
        >
          다음 시도
        </Link>
      )}
      {nextIds.length > 1 && (
        <details className="relative">
          <summary className="cursor-pointer list-none rounded-full border border-slate-200 px-2.5 py-1 font-medium text-slate-600 hover:bg-slate-50">
            다음 시도 ({nextIds.length})
          </summary>
          <div className="absolute left-0 z-20 mt-1 grid min-w-28 gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-lg">
            {nextIds.map((id, index) => (
              <Link
                key={id}
                href={`/presentation?id=${encodeURIComponent(id)}`}
                className="rounded-md px-2 py-1.5 text-slate-600 hover:bg-slate-50"
              >
                다음 시도 {index + 1}
              </Link>
            ))}
          </div>
        </details>
      )}
      {restartAvailable && !editor && (
        <button
          type="button"
          disabled={Boolean(restartBlockedReason) || loadingEditor || continuing}
          title={restartBlockedReason ?? undefined}
          onClick={() => void openEditor()}
          className="min-h-10 rounded-full border border-slate-200 px-4 py-2 font-medium text-slate-800 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
        >
          {loadingEditor ? "요청 불러오는 중" : "요청 수정"}
        </button>
      )}
      {restartAvailable && editor && (
        <form
          className="w-full rounded-2xl bg-slate-50 p-3 text-sm text-slate-800"
          onSubmit={continueToSettings}
          data-testid="workspace-attempt-editor"
        >
          <label
            className="block text-xs font-semibold text-slate-700"
            htmlFor={`attempt-prompt-${presentationId}`}
          >
            요청 수정
          </label>
          <textarea
            id={`attempt-prompt-${presentationId}`}
            value={editor.prompt}
            onChange={(event) =>
              setEditor((current) =>
                current ? { ...current, prompt: event.target.value } : current,
              )
            }
            rows={4}
            className="mt-2 max-h-44 min-h-24 w-full resize-y rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            aria-describedby={
              editor.attachmentNames.length > 0
                ? `attempt-attachments-${presentationId}`
                : undefined
            }
          />
          {editor.attachmentNames.length > 0 && (
            <p
              id={`attempt-attachments-${presentationId}`}
              className="mt-2 text-xs leading-5 text-slate-600"
            >
              이전 첨부: {editor.attachmentNames.join(", ")}
              <br />
              파일 접근 권한과 준비 상태를 다시 확인해야 하므로 다음 화면에서
              사용할 파일을 다시 첨부해 주세요.
            </p>
          )}
          <p className="mt-2 text-xs leading-5 text-slate-500">
            다음 화면에서 기존 생성 설정을 확인한 뒤 명시적으로 새 시도를
            시작합니다.
          </p>
          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setEditor(null);
                setEditorError(null);
              }}
              disabled={continuing}
              className="min-h-10 rounded-full px-4 py-2 font-medium text-slate-600 hover:bg-slate-100 disabled:text-slate-400"
            >
              변경 취소
            </button>
            <button
              type="submit"
              disabled={
                continuing || !restartAvailable || Boolean(restartBlockedReason)
              }
              title={restartBlockedReason ?? undefined}
              className="min-h-10 rounded-full bg-blue-600 px-4 py-2 font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {continuing ? "설정 여는 중" : "수정 내용으로 설정 확인"}
            </button>
          </div>
        </form>
      )}
      {restartAvailable && restartBlockedReason && (
        <span className="w-full text-right text-[11px] text-slate-500">
          {restartBlockedReason}
        </span>
      )}
      {restartAvailable && editorError && (
        <p className="w-full text-right text-[11px] text-red-700" role="alert">
          {editorError}
        </p>
      )}
    </nav>
  );
}
