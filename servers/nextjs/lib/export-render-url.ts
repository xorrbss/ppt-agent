type ExportRenderEnvironment = {
  NEXT_PUBLIC_URL?: string;
  NEXT_INTERNAL_URL?: string;
  PROXY_PORT?: string;
};

/**
 * Resolve the URL that the bundled headless browser uses to render /pdf-maker.
 *
 * The renderer needs both Next.js routes and /app_data assets, so it must use the
 * same single-origin proxy as the browser instead of talking to Next.js directly.
 * NEXT_PUBLIC_URL remains the explicit deployment override and the integrated
 * container injects NEXT_INTERNAL_URL for its nginx listener.
 */
export function resolveExportRenderBaseUrl(
  env: NodeJS.ProcessEnv | ExportRenderEnvironment = process.env
): string {
  const configured =
    env.NEXT_PUBLIC_URL?.trim() || env.NEXT_INTERNAL_URL?.trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }

  const port = env.PROXY_PORT?.trim() || "5000";
  if (!/^\d+$/.test(port)) {
    throw new Error(
      "PROXY_PORT must be a numeric TCP port for presentation export."
    );
  }

  return `http://127.0.0.1:${port}`;
}
