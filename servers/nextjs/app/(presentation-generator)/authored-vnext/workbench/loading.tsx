"use client";

import { useTranslations } from "next-intl";

export default function AuthoredVnextWorkbenchLoading() {
  const t = useTranslations("authoredVnext.workbench.state");
  return (
    <main className="grid min-h-screen place-items-center bg-slate-950 text-slate-100">
      <div aria-live="polite" className="rounded-2xl border border-slate-700 bg-slate-900 px-6 py-5 text-sm">
        {t("loadingTitle")}
      </div>
    </main>
  );
}
