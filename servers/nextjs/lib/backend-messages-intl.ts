/**
 * next-intl binding for backend message keys (see `backend-messages.ts`).
 *
 * Components inside the intl provider pass `useLocale()`; plain modules such
 * as the API error handler resolve the locale from the UI locale cookie.
 */

import { createTranslator } from "next-intl";

import { DEFAULT_UI_LOCALE, readUiLocaleCookie, type UiLocale } from "@/i18n/locale";
import { getUiMessages } from "@/i18n/messages";

import {
  resolveBackendMessage,
  type BackendMessage,
  type BackendTranslator,
} from "./backend-messages";

const translators = new Map<UiLocale, BackendTranslator>();

export function currentUiLocale(): UiLocale {
  if (typeof document === "undefined") return DEFAULT_UI_LOCALE;
  try {
    return readUiLocaleCookie(document.cookie);
  } catch {
    return DEFAULT_UI_LOCALE;
  }
}

export function backendTranslator(locale: UiLocale): BackendTranslator {
  const cached = translators.get(locale);
  if (cached) return cached;
  const t = createTranslator({
    locale,
    messages: getUiMessages(locale),
    namespace: "backend",
    // A key the backend emits before the catalog learns it is a fallback
    // case handled by `resolveBackendMessage`, not a console error.
    onError: () => {},
  });
  const format = t as unknown as (
    id: string,
    params?: Record<string, unknown>,
  ) => string;
  const translator: BackendTranslator = {
    has: (id) => t.has(id as never),
    format: (id, params) => format(id, params),
  };
  translators.set(locale, translator);
  return translator;
}

export function translateBackendMessage(
  message: BackendMessage | undefined,
  locale: UiLocale = currentUiLocale(),
): string | undefined {
  return resolveBackendMessage(backendTranslator(locale), message);
}
