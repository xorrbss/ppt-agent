// Same-origin API and static assets: nginx proxies /api/v1, /static, /app_data to fixed internal ports.

function withLeadingSlash(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

function isAbsoluteHttpUrl(path: string): boolean {
  return /^https?:\/\//i.test(path);
}

/** Browser: current site origin. Server render: localhost FastAPI (dev only). */
export function getFastAPIUrl(): string {
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  return "http://127.0.0.1:8000";
}

/** Use relative URLs; nginx serves /api/v1 on the same host as the UI. */
export function getApiUrl(path: string): string {
  if (isAbsoluteHttpUrl(path)) {
    return path;
  }
  return withLeadingSlash(path);
}

/** Keep /static and /app_data as same-origin paths the browser resolves. */
export function resolveBackendAssetUrl(path?: string): string {
  if (!path) return "";

  const trimmed = path.trim();
  if (!trimmed) return "";

  if (
    trimmed.startsWith("data:") ||
    trimmed.startsWith("blob:") ||
    trimmed.startsWith("file:")
  ) {
    return trimmed;
  }

  if (isAbsoluteHttpUrl(trimmed)) {
    return trimmed;
  }

  return withLeadingSlash(trimmed);
}
