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

// Utility to construct full API URL
export function getApiUrl(path: string): string {
  const baseUrl = getFastAPIUrl();
  // Remove leading slash if present to avoid double slashes
  const cleanPath = path.startsWith('/') ? path.slice(1) : path;
  return `${baseUrl}/${cleanPath}`;
}