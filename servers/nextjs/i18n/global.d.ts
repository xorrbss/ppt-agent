import type { UiLocale } from "./locale";
import type { UiMessages } from "./messages";

declare module "next-intl" {
  interface AppConfig {
    Locale: UiLocale;
    Messages: UiMessages;
  }
}
