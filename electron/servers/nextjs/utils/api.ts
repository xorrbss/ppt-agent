// Utility to get the FastAPI base URL
export function getFastAPIUrl(): string {
  // Prefer Electron-preload env when available
  if (typeof window !== "undefined" && (window as any).env?.NEXT_PUBLIC_FAST_API) {
    return (window as any).env.NEXT_PUBLIC_FAST_API;
  }

  // In Electron, NEXT_PUBLIC_FAST_API is set by setupEnv in main.ts
  if (process.env.NEXT_PUBLIC_FAST_API) {
    return process.env.NEXT_PUBLIC_FAST_API;
  }

  // Safe Electron fallback to local FastAPI
  return "http://127.0.0.1:8000";
}

function isAbsoluteHttpUrl(path: string): boolean {
  return /^https?:\/\//i.test(path);
}

function withLeadingSlash(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

function isElectronRuntime(): boolean {
  return typeof window !== "undefined" && !!(window as any).electron;
}

// Utility to construct API URL that works in both web and Electron.
export function getApiUrl(path: string): string {
  if (isAbsoluteHttpUrl(path)) {
    return path;
  }

  const normalizedPath = withLeadingSlash(path);
  const isFastApiEndpoint = normalizedPath.startsWith("/api/v1/");

  // In web/docker, /api/v1 is typically reverse-proxied by the web server.
  // In Electron, Next and FastAPI run on different ports, so use FastAPI base URL.
  if (isFastApiEndpoint && (isElectronRuntime() || !!process.env.NEXT_PUBLIC_FAST_API)) {
    return `${getFastAPIUrl()}${normalizedPath}`;
  }

  return normalizedPath;
}