import { existsSync } from "node:fs";
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
  const match = candidates.find((candidate) => existsSync(candidate));
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
