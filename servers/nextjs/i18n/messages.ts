// The attribute lets Node's ESM loader (node --test) import the catalog.
import en from "./messages/en.json" with { type: "json" };
import ko from "./messages/ko.json" with { type: "json" };
import type { UiLocale } from "./locale";

export type UiMessages = typeof en;

const UI_MESSAGES: Record<UiLocale, UiMessages> = {
  en,
  ko,
};

export function getUiMessages(locale: UiLocale): UiMessages {
  return UI_MESSAGES[locale];
}

export const uiMessageCatalogs = UI_MESSAGES;
