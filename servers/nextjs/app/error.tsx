"use client";

import { useEffect } from "react";
import { useTranslations } from "next-intl";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations();

  useEffect(() => {
    console.error("Application error:", error);
  }, [error]);

  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 p-6 text-center text-slate-950">
      <section className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-8 shadow-lg">
        <h1 className="text-2xl font-bold">{t("globalError.title")}</h1>
        <p className="mt-3 text-sm text-slate-600">
          {t("globalError.description")}
        </p>
        {error.digest ? (
          <p className="mt-2 text-xs text-slate-500">
            {t("globalError.details", { digest: error.digest })}
          </p>
        ) : null}
        <button
          type="button"
          className="mt-6 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
          onClick={reset}
        >
          {t("common.retry")}
        </button>
      </section>
    </main>
  );
}
