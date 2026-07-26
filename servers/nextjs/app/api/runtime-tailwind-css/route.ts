import { createHash } from "node:crypto";
import { NextResponse } from "next/server";
import tailwindConfig from "@/tailwind.config";
import {
  compileRuntimeTailwindCss,
  normalizeRuntimeTailwindSources,
} from "@/lib/runtime-tailwind-css";

export const runtime = "nodejs";

const cssCache = new Map<string, string>();
const MAX_CACHE_ENTRIES = 128;

function cacheKey(sources: string[]): string {
  return createHash("sha256").update(sources.join("\u0000")).digest("hex");
}

export async function POST(request: Request) {
  try {
    const body = (await request.json()) as { sources?: unknown };
    const sources = normalizeRuntimeTailwindSources(body.sources);
    const key = cacheKey(sources);
    let css = cssCache.get(key);

    if (css === undefined) {
      css = await compileRuntimeTailwindCss(sources, tailwindConfig);
      if (cssCache.size >= MAX_CACHE_ENTRIES) {
        const oldestKey = cssCache.keys().next().value;
        if (oldestKey) cssCache.delete(oldestKey);
      }
      cssCache.set(key, css);
    }

    return NextResponse.json(
      { css, key },
      { headers: { "Cache-Control": "private, max-age=3600" } }
    );
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unable to compile Tailwind CSS";
    const status =
      error instanceof TypeError || error instanceof RangeError ? 400 : 500;
    return NextResponse.json({ error: message }, { status });
  }
}
