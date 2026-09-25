"use client";

import React from "react";
import { useTranslations } from "next-intl";

const Loading = () => {
  const t = useTranslations("dashboardHome");
  return (
  <div className="relative mx-auto min-h-screen w-full max-w-[1280px] px-5 pb-12 pt-8 sm:px-8 lg:px-12" role="status" aria-label={t("loading")}>
    <div aria-hidden="true" className="mx-auto flex max-w-[820px] animate-pulse flex-col items-center pb-12 pt-8 sm:pb-16 sm:pt-14">
      <div className="mb-5 h-11 w-11 rounded-2xl bg-zinc-100" />
      <div className="h-10 w-64 max-w-full rounded-xl bg-zinc-200/70" />
      <div className="mt-3 h-5 w-80 max-w-full rounded bg-zinc-100" />
      <div className="mt-8 w-full rounded-3xl border border-zinc-200 bg-white p-5">
        <div className="h-[128px] rounded-2xl bg-zinc-50" />
        <div className="mt-3 flex justify-between gap-4">
          <div className="h-10 w-28 rounded-full bg-zinc-100" />
          <div className="h-10 w-28 rounded-full bg-zinc-200" />
        </div>
      </div>
      <div className="mt-5 flex gap-2.5">
        <div className="h-10 w-28 rounded-full bg-zinc-100" />
        <div className="h-10 w-32 rounded-full bg-zinc-100" />
      </div>
    </div>
    <div aria-hidden="true" className="border-t border-zinc-200/70 pt-8">
      <div className="mb-5 flex justify-between">
        <div className="h-6 w-24 rounded bg-zinc-200/70" />
        <div className="h-10 w-48 rounded-xl bg-zinc-100" />
      </div>
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, index) => (
          <div key={index} className="animate-pulse overflow-hidden rounded-2xl border border-zinc-200 bg-white">
            <div className="aspect-video bg-zinc-100" />
            <div className="space-y-2 border-t border-zinc-200/70 p-4">
              <div className="h-4 w-36 rounded bg-zinc-200/70" />
              <div className="h-3 w-20 rounded bg-zinc-100" />
            </div>
          </div>
        ))}
      </div>
    </div>
  </div>
);
};

export default Loading;
