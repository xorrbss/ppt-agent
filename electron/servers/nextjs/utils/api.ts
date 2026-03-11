// Utility to get the FastAPI base URL
export function getFastAPIUrl(): string {
  // In Electron environment, use the exposed env variable
  if (typeof window !== 'undefined' && (window as any).env) {
    return (window as any).env.NEXT_PUBLIC_FAST_API || '';
  }
  
  // Prefer explicit env var when available in any mode
  if (process.env.NEXT_PUBLIC_FAST_API) {
    return process.env.NEXT_PUBLIC_FAST_API;
  }

  // Electron mode: direct access to FastAPI
  if (typeof window !== 'undefined' && (window as any).electron) {
    return 'http://127.0.0.1:8000';
  }

  // Docker/web mode: use current origin (goes through nginx)
  if (typeof window !== 'undefined') {
    return window.location.origin;
  }
  // Server-side fallback
  return 'http://127.0.0.1:8000';
}

// Utility to construct full API URL
export function getApiUrl(path: string): string {
  const baseUrl = getFastAPIUrl();
  // Remove leading slash if present to avoid double slashes
  const cleanPath = path.startsWith('/') ? path.slice(1) : path;
  return `${baseUrl}/${cleanPath}`;
}