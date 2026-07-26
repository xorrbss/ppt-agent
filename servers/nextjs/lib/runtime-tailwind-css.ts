import postcss from "postcss";
import tailwindcss from "tailwindcss";
import type { Config } from "tailwindcss";

const RUNTIME_TAILWIND_INPUT = [
  "@tailwind components;",
  "@tailwind utilities;",
].join("\n");

export const MAX_RUNTIME_TAILWIND_SOURCES = 64;
export const MAX_RUNTIME_TAILWIND_SOURCE_BYTES = 2_000_000;

export function normalizeRuntimeTailwindSources(value: unknown): string[] {
  if (!Array.isArray(value)) {
    throw new TypeError("sources must be an array");
  }
  if (value.length > MAX_RUNTIME_TAILWIND_SOURCES) {
    throw new RangeError(
      `sources must contain at most ${MAX_RUNTIME_TAILWIND_SOURCES} entries`
    );
  }

  const sources = value.filter(
    (source): source is string => typeof source === "string" && source.length > 0
  );
  const totalBytes = sources.reduce(
    (total, source) => total + Buffer.byteLength(source, "utf8"),
    0
  );
  if (totalBytes > MAX_RUNTIME_TAILWIND_SOURCE_BYTES) {
    throw new RangeError(
      `sources must be at most ${MAX_RUNTIME_TAILWIND_SOURCE_BYTES} bytes`
    );
  }
  return sources;
}

export async function compileRuntimeTailwindCss(
  sources: string[],
  baseConfig: Config
): Promise<string> {
  if (sources.length === 0) {
    return "";
  }

  const runtimeConfig: Config = {
    ...baseConfig,
    content: sources.map((raw) => ({ raw, extension: "tsx" })),
    // The application already installs Tailwind's base layer globally. Runtime
    // compilation is utilities-only and must not read or duplicate preflight.
    corePlugins: { preflight: false },
  };
  const result = await postcss([tailwindcss(runtimeConfig)]).process(
    RUNTIME_TAILWIND_INPUT,
    { from: undefined }
  );
  return result.css;
}
