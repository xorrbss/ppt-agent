/**
 * The process-wide Google Fonts collector used by the export pipeline and the
 * authored-hybrid QA scripts.
 *
 * Kept out of google-fonts-collector so that module stays free of app-data and
 * environment coupling and remains constructible with an arbitrary cache
 * directory in tests, while every runtime caller still shares one on-disk cache
 * and one enable/disable decision.
 *
 * Construction is deferred to first use on purpose: resolveAppDataDirectory
 * throws when APP_DATA_DIRECTORY/USER_CONFIG_PATH are unset, and `next build`
 * evaluates this module while collecting page data for the export route, where
 * neither is configured. Only an actual export needs the cache directory.
 */

import path from "node:path";

import { resolveAppDataDirectory } from "@/lib/app-data-directory";

import { GoogleFontsCollector } from "./google-fonts-collector.ts";

/**
 * `DISABLE_GOOGLE_FONTS_COLLECTION=true` keeps authored-hybrid export fully
 * offline. Decks that reference Google Fonts then render with the local
 * fallback stacks from font-policy instead of reaching fonts.googleapis.com.
 * Matches the existing DISABLE_* operator switches in this project.
 */
function collectionEnabled(): boolean {
  const raw = (process.env.DISABLE_GOOGLE_FONTS_COLLECTION ?? "")
    .trim()
    .toLowerCase();
  return raw !== "true" && raw !== "1";
}

let collector: GoogleFontsCollector | null = null;

export function getGoogleFontsCollector(): GoogleFontsCollector {
  if (!collector) {
    collector = new GoogleFontsCollector({
      enabled: collectionEnabled(),
      cacheDirectory: path.join(
        resolveAppDataDirectory(),
        "authored-hybrid",
        "google-fonts"
      ),
    });
  }
  return collector;
}
