"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { AlertTriangle, Loader2 } from "lucide-react";

import { useGenerationActivity } from "@/app/(presentation-generator)/hooks/useGenerationActivity";
import { serverNowMs } from "@/app/(presentation-generator)/services/api/generation-activity-model";
import {
  describeGenerationActivityItem,
  presentationHref,
} from "@/app/(presentation-generator)/services/api/generation-activity-view";

/** In-flight generations for the signed-in owner; renders nothing when there are none. */
export function GenerationActivitySection() {
  const t = useTranslations("generationActivity");
  const snapshot = useGenerationActivity();
  // Re-render between polls so "N minutes ago" keeps moving.
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((value) => value + 1), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  if (!snapshot || snapshot.items.length === 0) return null;
  const nowMs = serverNowMs(snapshot);

  return (
    <section
      aria-label={t("sectionAria")}
      data-testid="generation-activity-section"
      className="relative z-10 mb-8 rounded-2xl border border-blue-100 bg-blue-50/60 p-4"
    >
      <h2 className="mb-3 flex items-center gap-2 text-base font-semibold tracking-tight text-zinc-800">
        <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin motion-reduce:animate-none text-blue-600" />
        {t("sectionTitle")}
      </h2>
      <ul className="space-y-2">
        {snapshot.items.map((item) => {
          const view = describeGenerationActivityItem(item, nowMs);
          const label = item.presentationTitle ?? item.sourceFileName ?? t("untitled");
          return (
            <li
              key={item.operationId}
              data-testid="generation-activity-row"
              className="flex flex-col gap-1 rounded-xl border border-zinc-200 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-zinc-900" title={label}>{label}</p>
                <p className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-zinc-600">
                  {view.cancelling ? (
                    <span>{t("cancelling")}</span>
                  ) : view.queued ? (
                    <span>
                      {view.queuePosition !== null
                        ? t("queued", { position: view.queuePosition })
                        : t("queuedNoPosition")}
                    </span>
                  ) : (
                    <>
                      <span>
                        {view.pages.total !== null
                          ? t("pagesWritten", { authored: view.pages.authored, total: view.pages.total })
                          : t("pagesWrittenNoTotal", { authored: view.pages.authored })}
                      </span>
                      {view.progressMinutes !== null && (
                        <span>
                          {view.progressMinutes < 1
                            ? t("lastProgressJustNow")
                            : t("lastProgress", { minutes: view.progressMinutes })}
                        </span>
                      )}
                    </>
                  )}
                </p>
                {view.stalled && view.stalledMinutes !== null && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-amber-800" role="status">
                    <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
                    {t("stalled", { minutes: view.stalledMinutes })}
                  </p>
                )}
              </div>
              <Link
                href={presentationHref(item.presentationId)}
                prefetch={false}
                className="mt-2 inline-flex h-8 shrink-0 items-center rounded-full border border-zinc-200 px-3 text-xs font-medium text-zinc-700 transition hover:bg-zinc-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 sm:mt-0"
              >
                {t("open")}
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export default GenerationActivitySection;
