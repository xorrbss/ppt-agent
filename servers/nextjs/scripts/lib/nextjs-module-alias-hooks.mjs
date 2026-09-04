/**
 * Resolve the project's `@/` TypeScript path alias for plain Node processes.
 *
 * Next.js resolves `@/...` through tsconfig `paths`. Standalone Node entry
 * points that import the same modules (for example the sessionless
 * authored-hybrid export worker) have no bundler, so they install these
 * synchronous module hooks before importing anything under `lib/`.
 *
 * Hooks must be installed before the aliased module graph is resolved, so
 * callers import this module first and then use dynamic `import()`.
 */

import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const CANDIDATE_SUFFIXES = ["", ".ts", ".tsx", ".mjs", ".js", "/index.ts", "/index.mjs", "/index.js"];

let installed = false;

/** Install `@/` resolution rooted at the Next.js server directory. */
export function installNextjsModuleAliasHooks() {
  if (installed) return;
  installed = true;
  const nextjsRoot = pathToFileURL(
    `${path.resolve(fileURLToPath(new URL("../..", import.meta.url)))}${path.sep}`,
  ).href;
  registerHooks({
    resolve(specifier, context, next) {
      if (!specifier.startsWith("@/")) return next(specifier, context);
      for (const suffix of CANDIDATE_SUFFIXES) {
        const candidate = new URL(specifier.slice(2) + suffix, nextjsRoot).href;
        if (existsSync(fileURLToPath(candidate))) return next(candidate, context);
      }
      return next(specifier, context);
    },
  });
}
