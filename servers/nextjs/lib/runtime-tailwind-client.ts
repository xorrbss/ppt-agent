const pendingStyles = new Map<string, Promise<void>>();

function sourceKey(sources: string[]): string {
  let hash = 2166136261;
  const value = sources.join("\u0000");
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${value.length}-${(hash >>> 0).toString(36)}`;
}

/**
 * Compile user-authored template utilities on the local Next server and install
 * them once in the document. Generated templates can contain arbitrary-value
 * classes that cannot be discovered by Tailwind during the application build.
 */
export async function ensureRuntimeTailwindCss(
  rawSources: Array<string | null | undefined>
): Promise<void> {
  if (typeof document === "undefined") return;

  const sources = rawSources.filter(
    (source): source is string => typeof source === "string" && source.length > 0
  );
  if (sources.length === 0) return;

  const key = sourceKey(sources);
  const styleId = `runtime-tailwind-${key}`;
  if (document.getElementById(styleId)) return;

  const existing = pendingStyles.get(key);
  if (existing) return existing;

  const request = (async () => {
    const response = await fetch("/api/runtime-tailwind-css", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sources }),
    });
    if (!response.ok) {
      throw new Error(`Runtime Tailwind compilation failed (${response.status})`);
    }
    const payload = (await response.json()) as { css?: unknown };
    if (typeof payload.css !== "string") {
      throw new Error("Runtime Tailwind compilation returned invalid CSS");
    }

    const style = document.createElement("style");
    style.id = styleId;
    style.dataset.runtimeTailwind = "true";
    style.textContent = payload.css;
    document.head.appendChild(style);
  })();

  pendingStyles.set(key, request);
  try {
    await request;
  } finally {
    pendingStyles.delete(key);
  }
}
