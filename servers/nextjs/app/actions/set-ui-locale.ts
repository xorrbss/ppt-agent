"use server";

import { cookies } from "next/headers";

import {
  isUiLocale,
  UI_LOCALE_COOKIE,
  UI_LOCALE_MAX_AGE_SECONDS,
  type UiLocale,
} from "@/i18n/locale";

export async function setUiLocale(value: string): Promise<UiLocale> {
  if (!isUiLocale(value)) {
    throw new Error("Unsupported UI locale");
  }

  const cookieStore = await cookies();
  cookieStore.set({
    name: UI_LOCALE_COOKIE,
    value,
    httpOnly: false,
    maxAge: UI_LOCALE_MAX_AGE_SECONDS,
    path: "/",
    sameSite: "lax",
  });

  return value;
}
