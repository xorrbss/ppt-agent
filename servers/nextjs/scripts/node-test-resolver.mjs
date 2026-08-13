import { existsSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const EXTENSIONS = [".ts", ".tsx", ".mjs", ".js"];

function resolveFileUrl(baseUrl) {
  const filePath = fileURLToPath(baseUrl);
  const candidates = [
    filePath,
    ...EXTENSIONS.map((extension) => `${filePath}${extension}`),
    ...EXTENSIONS.map((extension) => path.join(filePath, `index${extension}`)),
  ];
  // A bare alias may name a directory with an index module. Returning that
  // directory URL makes Node try to read it as a source file (EISDIR), rather
  // than continuing to the index candidates below.
  const match = candidates.find(
    (candidate) => existsSync(candidate) && statSync(candidate).isFile(),
  );
  return match ? pathToFileURL(match).href : null;
}

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("@/")) {
    const rootUrl = pathToFileURL(path.join(process.cwd(), specifier.slice(2)));
    const resolved = resolveFileUrl(rootUrl);
    if (resolved) return { shortCircuit: true, url: resolved };
  }

  if (specifier.startsWith(".") && context.parentURL) {
    const resolved = resolveFileUrl(new URL(specifier, context.parentURL));
    if (resolved) return { shortCircuit: true, url: resolved };
  }

  return nextResolve(specifier, context);
}
