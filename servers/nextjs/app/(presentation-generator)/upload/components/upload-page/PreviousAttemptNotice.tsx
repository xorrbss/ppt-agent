"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import type { usePreviousGenerationAttempt } from "./usePreviousGenerationAttempt";

export function PreviousAttemptNotice({ attempt }: { attempt: ReturnType<typeof usePreviousGenerationAttempt> }) {
  if (!attempt.requestedId) return null;
  return <aside className="mx-auto mb-5 max-w-3xl rounded-2xl border border-neutral-200 bg-neutral-50 px-5 py-4 text-sm text-neutral-700">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="font-medium">이전 요청을 수정해서 새로 만들기</p>
      <Link className="text-xs underline underline-offset-4" href={`/presentation?id=${encodeURIComponent(attempt.requestedId)}`}>이전 대화 보기</Link>
    </div>
    {attempt.error ? <div role="alert" className="mt-2 flex flex-wrap items-center gap-3"><p>{attempt.error}</p><Button type="button" variant="outline" size="sm" onClick={attempt.retry}>다시 확인</Button></div>
      : !attempt.info ? <p role="status" className="mt-2">이전 요청과 작업 상태를 확인하고 있습니다.</p>
      : <>
        <p className="mt-2">원하는 내용과 생성 설정을 확인해 주세요. 이전 대화는 보존되며 새 시도에서 다시 생성합니다.</p>
        {attempt.info.attachment_file_names.length > 0 && <p className="mt-2 text-xs leading-6">이전 첨부: {attempt.info.attachment_file_names.join(", ")}<br />사용할 파일을 다시 첨부해 주세요. 이전 첨부를 자동으로 재사용하지 않습니다.</p>}
      </>}
  </aside>;
}
