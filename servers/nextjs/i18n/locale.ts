export const SUPPORTED_UI_LOCALES = ["ko", "en"] as const;

export type UiLocale = (typeof SUPPORTED_UI_LOCALES)[number];

export const DEFAULT_UI_LOCALE: UiLocale = "ko";
export const UI_LOCALE_COOKIE = "presenton_ui_locale";
export const UI_LOCALE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

export function isUiLocale(value: unknown): value is UiLocale {
  return (
    typeof value === "string" &&
    (SUPPORTED_UI_LOCALES as readonly string[]).includes(value)
  );
}

export function resolveUiLocale(value: unknown): UiLocale {
  return isUiLocale(value) ? value : DEFAULT_UI_LOCALE;
}

export function readUiLocaleCookie(cookieHeader: string): UiLocale {
  const encodedName = `${encodeURIComponent(UI_LOCALE_COOKIE)}=`;
  const rawValue = cookieHeader
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(encodedName))
    ?.slice(encodedName.length);

  if (!rawValue) return DEFAULT_UI_LOCALE;

  try {
    return resolveUiLocale(decodeURIComponent(rawValue));
  } catch {
    return DEFAULT_UI_LOCALE;
  }
}
