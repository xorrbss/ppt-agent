"use client";

import { useEffect, useState } from "react";

import en from "@/i18n/messages/en.json";
import ko from "@/i18n/messages/ko.json";
import {
  DEFAULT_UI_LOCALE,
  readUiLocaleCookie,
  type UiLocale,
} from "@/i18n/locale";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [locale, setLocale] = useState<UiLocale | null>(null);

  useEffect(() => {
    console.error("Global error:", error);
  }, [error]);

  useEffect(() => {
    const nextLocale = readUiLocaleCookie(document.cookie);
    document.documentElement.lang = nextLocale;
    setLocale(nextLocale);
  }, []);

  const copy = locale === "en" ? en.globalError : ko.globalError;

  return (
    <html lang={locale ?? DEFAULT_UI_LOCALE}>
      <body className="m-0 bg-slate-50 font-sans text-slate-950">
        {locale ? (
          <main className="grid min-h-screen place-items-center p-6 text-center">
            <section className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-8 shadow-lg">
              <h1 className="text-2xl font-bold">{copy.title}</h1>
              <p className="mt-3 text-sm text-slate-600">{copy.description}</p>
              {error.digest ? (
                <p className="mt-2 text-xs text-slate-500">
                  {copy.details.replace("{digest}", error.digest)}
                </p>
              ) : null}
              <button
                type="button"
                className="mt-6 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2"
                onClick={reset}
              >
                {locale === "en" ? en.common.retry : ko.common.retry}
              </button>
            </section>
          </main>
        ) : (
          <main aria-busy="true" className="grid min-h-screen place-items-center">
            <span className="size-8 animate-spin rounded-full border-4 border-slate-200 border-t-indigo-600" />
          </main>
        )}
      </body>
    </html>
  );
}
