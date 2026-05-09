// Utility to get the FastAPI base URL
export function getFastAPIUrl(): string {
  if (typeof window !== "undefined" && window.env?.NEXT_PUBLIC_FAST_API) {
    return window.env.NEXT_PUBLIC_FAST_API;
  }

  if (process.env.NEXT_PUBLIC_FAST_API) {
    return process.env.NEXT_PUBLIC_FAST_API;
  }

  const queryFastApiUrl = getFastApiUrlFromQuery();
  if (queryFastApiUrl) {
    return queryFastApiUrl;
  }

  // Docker/web runtime: route backend assets and APIs through current origin
  // (nginx reverse-proxies /api/v1, /app_data, /static).
  if (typeof window !== "undefined") {
    return window.location.origin;
  }

  return "http://127.0.0.1:5000";
}

function getFastApiUrlFromQuery(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const params = new URLSearchParams(window.location.search);
    const value = params.get("fastapiUrl");
    if (!value) return null;

    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}

function isAbsoluteHttpUrl(path: string): boolean {
  return /^https?:\/\//i.test(path);
}

function withLeadingSlash(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

function isElectronRuntime(): boolean {
  return typeof window !== "undefined" && !!window.electron;
}

// Utility to construct API URL for Docker/web runtime.
export function getApiUrl(path: string): string {
  if (isAbsoluteHttpUrl(path)) {
    return path;
  }

  const normalizedPath = withLeadingSlash(path);
  const isFastApiEndpoint = normalizedPath.startsWith("/api/v1/");
  const hasConfiguredFastApi = !!process.env.NEXT_PUBLIC_FAST_API;
  const hasWindowFastApi =
    typeof window !== "undefined" && !!window.env?.NEXT_PUBLIC_FAST_API;
  const hasQueryFastApi = !!getFastApiUrlFromQuery();

  // In web/docker, /api/v1 is typically reverse-proxied by the web server.
  // Keep browser requests same-origin so session cookies stay attached by default.
  // For Electron split-port runtime and query-overrides, target FastAPI directly.
  // Server-side callers can still use configured FastAPI base URLs directly.
  if (
    isFastApiEndpoint &&
    (isElectronRuntime() || hasWindowFastApi || hasQueryFastApi)
  ) {
    return `${getFastAPIUrl()}${normalizedPath}`;
  }

  if (
    isFastApiEndpoint &&
    typeof window === "undefined" &&
    hasConfiguredFastApi
  ) {
    return `${getFastAPIUrl()}${normalizedPath}`;
  }

  return normalizedPath;
}

/**
 * getApiUrl may return a path without host (e.g. `/api/v1/...`). A single-argument
 * `new URL("/api/...")` call is invalid; use this before `new URL(..., ...)`-style
 * builds or to obtain an absolute string for `URL` + `searchParams`.
 */
export function buildAbsoluteApiRequestUrl(
  path: string,
  baseForRelative: string = typeof window !== "undefined" &&
  window.location?.origin
    ? window.location.origin
    : "http://127.0.0.1:5000"
): string {
  const resolved = getApiUrl(path);
  if (isAbsoluteHttpUrl(resolved)) {
    return resolved;
  }
  return new URL(resolved, baseForRelative).toString();
}

function hasBackendAssetPrefix(path: string): boolean {
  return path.startsWith("/static/") || path.startsWith("/app_data/");
}

function toBackendServedPath(rawPath: string): string {
  const normalized = rawPath.replace(/\\/g, "/");

  const appDataIdx = normalized.indexOf("/app_data/");
  if (appDataIdx !== -1) {
    return normalized.slice(appDataIdx);
  }

  const staticIdx = normalized.indexOf("/static/");
  if (staticIdx !== -1) {
    return normalized.slice(staticIdx);
  }

  const imagesIdx = normalized.lastIndexOf("/images/");
  if (imagesIdx !== -1) {
    return `/app_data${normalized.slice(imagesIdx)}`;
  }

  const uploadsIdx = normalized.lastIndexOf("/uploads/");
  if (uploadsIdx !== -1) {
    return `/app_data${normalized.slice(uploadsIdx)}`;
  }

  const fontsIdx = normalized.lastIndexOf("/fonts/");
  if (fontsIdx !== -1) {
    return `/app_data${normalized.slice(fontsIdx)}`;
  }

  return normalized;
}

// Resolve backend-served asset paths to the FastAPI origin.
export function resolveBackendAssetUrl(path?: string): string {
  if (!path) return "";

  const trimmedPath = path.trim();
  if (!trimmedPath) return "";

  if (trimmedPath.startsWith("data:") || trimmedPath.startsWith("blob:")) {
    return trimmedPath;
  }

  if (trimmedPath.startsWith("file:")) {
    try {
      const parsed = new URL(trimmedPath);
      const servedPath = toBackendServedPath(decodeURIComponent(parsed.pathname));
      if (hasBackendAssetPrefix(servedPath)) {
        return `${getFastAPIUrl()}${servedPath}`;
      }
      return trimmedPath;
    } catch {
      return trimmedPath;
    }
  }

  if (isAbsoluteHttpUrl(trimmedPath)) {
    try {
      const parsed = new URL(trimmedPath);
      const servedPath = toBackendServedPath(parsed.pathname);
      if (hasBackendAssetPrefix(servedPath)) {
        return `${getFastAPIUrl()}${servedPath}${parsed.search}${parsed.hash}`;
      }
      return trimmedPath;
    } catch {
      return trimmedPath;
    }
  }

  const normalizedPath = withLeadingSlash(trimmedPath);
  const servedPath = toBackendServedPath(normalizedPath);
  if (hasBackendAssetPrefix(servedPath)) {
    return `${getFastAPIUrl()}${servedPath}`;
  }

  return trimmedPath;
}

export const normalizeBackendAssetUrls = <T,>(input: T): T => {
  if (Array.isArray(input)) {
    return input.map((item) => normalizeBackendAssetUrls(item)) as T;
  }

  if (input && typeof input === "object") {
    const normalized: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(input as Record<string, unknown>)) {
      normalized[key] =
        typeof value === "string"
          ? resolveBackendAssetUrl(value)
          : normalizeBackendAssetUrls(value);
    }
    return normalized as T;
  }

  return input;
};
