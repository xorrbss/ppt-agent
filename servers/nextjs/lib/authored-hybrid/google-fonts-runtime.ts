/**
 * The process-wide Google Fonts collector used by the export pipeline and the
 * authored-hybrid QA scripts.
 *
 * Kept out of google-fonts-collector so that module stays free of app-data
 * coupling and remains constructible with an arbitrary cache directory in
 * tests, while every runtime caller still shares one on-disk cache.
 */

import path from "node:path";

import { resolveAppDataDirectory } from "@/lib/app-data-directory";

import { GoogleFontsCollector } from "./google-fonts-collector.ts";

export const googleFontsCollector = new GoogleFontsCollector({
  cacheDirectory: path.join(
    resolveAppDataDirectory(),
    "authored-hybrid",
    "google-fonts"
  ),
});
