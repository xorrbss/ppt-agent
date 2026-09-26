import { createTranslator } from "next-intl";
import { resolveUiLocale } from "@/i18n/locale";
import { getUiMessages } from "@/i18n/messages";

// Flow helpers run outside React; use the active document locale at call time.
export const uploadExtraTranslator = () => {
  const locale = resolveUiLocale(typeof document === "undefined" ? undefined : document.documentElement.lang);
  return createTranslator({ locale, messages: getUiMessages(locale), namespace: "uploadExtras" });
};
