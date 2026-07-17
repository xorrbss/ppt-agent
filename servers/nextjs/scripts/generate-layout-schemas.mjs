// Build-time layout-schema generator.
//
// Compiles every built-in template group's Zod `Schema` to JSON once and writes a
// single artifact (`app/presentation-templates/layouts.generated.json`) that the
// FastAPI backend reads directly. This removes the per-request headless-scrape /
// runtime compile that `get_layout_by_name` used to resolve built-in layouts.
//
// It reuses the exact compile path the app uses at runtime
// (`buildBuiltinTemplateLayoutPayload` -> `compileTemplateSchema`), so the artifact
// cannot drift from that path — it IS that path, run at build time. Because
// `server-template-layouts.ts` is TypeScript with a `@/` path alias, we bundle it
// with esbuild (already a devDependency) before importing it.
import { build } from "esbuild";
import { readdir, writeFile, unlink } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(scriptDir, ".."); // servers/nextjs
// buildBuiltinTemplateLayoutPayload resolves templates from process.cwd(); pin it
// so the generator works regardless of how the build chain invokes it.
process.chdir(root);

const templatesDir = path.join(root, "app", "presentation-templates");
const artifactPath = path.join(templatesDir, "layouts.generated.json");
// Temp bundle must live inside the project so its externalized `@babel/*` / `zod`
// imports resolve from node_modules.
const tmpBundle = path.join(root, ".layout-schemas-gen.mjs");

async function loadBuilder() {
  await build({
    stdin: {
      contents:
        'export { buildBuiltinTemplateLayoutPayload } from "@/lib/server-template-layouts";',
      resolveDir: root,
      loader: "ts",
    },
    bundle: true,
    platform: "node",
    format: "esm",
    packages: "external", // leave node_modules (@babel, zod) external
    alias: { "@": root }, // resolve the @/ path alias
    outfile: tmpBundle,
  });
  try {
    const mod = await import(pathToFileURL(tmpBundle).href);
    return mod.buildBuiltinTemplateLayoutPayload;
  } finally {
    await unlink(tmpBundle).catch(() => {});
  }
}

async function main() {
  const buildBuiltinTemplateLayoutPayload = await loadBuilder();

  const entries = await readdir(templatesDir, { withFileTypes: true });
  const groups = entries
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort();

  const out = {};
  for (const group of groups) {
    // Groups with no per-file `Schema` (e.g. the adaptive renderer) return null and
    // are simply omitted — the backend resolves those in-process.
    const payload = await buildBuiltinTemplateLayoutPayload(group);
    if (payload) {
      out[group] = payload;
    }
  }

  await writeFile(artifactPath, JSON.stringify(out, null, 2) + "\n", "utf8");
  const names = Object.keys(out);
  console.log(
    `[layout-schemas] wrote ${names.length} group(s) -> ${path.relative(
      root,
      artifactPath,
    )} (${names.join(", ")})`,
  );
}

main().catch((err) => {
  console.error("[layout-schemas] generation failed:", err);
  process.exit(1);
});
