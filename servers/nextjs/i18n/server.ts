import { cookies } from "next/headers";
import { createTranslator } from "next-intl";

import { getUiMessages } from "./messages";
import {
  resolveUiLocale,
  UI_LOCALE_COOKIE,
  type UiLocale,
} from "./locale";

export async function getUiLocale(): Promise<UiLocale> {
  const cookieStore = await cookies();
  return resolveUiLocale(cookieStore.get(UI_LOCALE_COOKIE)?.value);
}

export async function getServerI18n() {
  const locale = await getUiLocale();
  const messages = getUiMessages(locale);
  return {
    locale,
    messages,
    t: createTranslator({ locale, messages }),
  };
}
