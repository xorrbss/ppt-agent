"use client";
import { useTranslations as useA2bTranslations } from "next-intl";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import type { usePreviousGenerationAttempt } from "./usePreviousGenerationAttempt";

export function PreviousAttemptNotice({ attempt }: { attempt: ReturnType<typeof usePreviousGenerationAttempt> }) {
  const tA2b = useA2bTranslations("uploadExtras");
  if (!attempt.requestedId) return null;
  return <aside className="mx-auto mb-5 max-w-3xl rounded-2xl border border-neutral-200 bg-neutral-50 px-5 py-4 text-sm text-neutral-700">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="font-medium">{tA2b("editThePreviousRequestAndCreateA")}</p>
      <Link className="text-xs underline underline-offset-4" href={`/presentation?id=${encodeURIComponent(attempt.requestedId)}`}>{tA2b("viewPreviousConversation")}</Link>
    </div>
    {attempt.error ? <div role="alert" className="mt-2 flex flex-wrap items-center gap-3"><p>{attempt.error}</p><Button type="button" variant="outline" size="sm" onClick={attempt.retry}>{tA2b("checkAgain")}</Button></div>
      : !attempt.info ? <p role="status" className="mt-2">{tA2b("checkingThePreviousRequestAndOperationStatus")}</p>
      : <>
        <p className="mt-2">{tA2b("checkTheDesiredContentAndGenerationSettings")}</p>
        {attempt.info.attachment_file_names.length > 0 && <p className="mt-2 text-xs leading-6">{tA2b("previousAttachment")} {attempt.info.attachment_file_names.join(", ")}<br />{tA2b("attachTheFilesToUseAgainPrevious")}</p>}
      </>}
  </aside>;
}
