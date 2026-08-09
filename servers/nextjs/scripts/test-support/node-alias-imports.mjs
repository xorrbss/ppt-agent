// `node --test` has no bundler, so modules that import through the `@/*`
// tsconfig alias cannot be loaded directly. Registering this synchronous
// resolve hook before the dynamic `import()` of such a module maps `@/x` to
// `<servers/nextjs>/x`, trying the extensions the bundler would try.
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

const APP_ROOT = new URL("../../", import.meta.url);
const EXTENSIONS = [
  "",
  ".ts",
  ".tsx",
  ".mts",
  ".mjs",
  ".js",
  "/index.ts",
  "/index.tsx",
];

let registered = false;

/**
 * Resolve `@/*` specifiers for the remainder of this process. Safe to call
 * more than once; only the first call registers hooks.
 */
export function registerAppAliasImports() {
  if (registered) return;
  registered = true;

  registerHooks({
    resolve(specifier, context, nextResolve) {
      if (!specifier.startsWith("@/")) return nextResolve(specifier, context);

      const base = new URL(specifier.slice(2), APP_ROOT);
      for (const extension of EXTENSIONS) {
        const candidate = new URL(base.href + extension);
        if (existsSync(fileURLToPath(candidate))) {
          return nextResolve(candidate.href, context);
        }
      }
      return nextResolve(base.href, context);
    },
  });
}
