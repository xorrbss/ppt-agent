import { createHash } from "node:crypto";

const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);
const FALSE_VALUES = new Set(["0", "false", "no", "off"]);

function booleanEnvironment(name: string, fallback: boolean): boolean {
  const value = process.env[name]?.trim().toLowerCase();
  if (!value) return fallback;
  if (TRUE_VALUES.has(value)) return true;
  if (FALSE_VALUES.has(value)) return false;
  throw new Error(`${name} must be a boolean value.`);
}

export interface AuthoredRendererIsolationPolicy {
  rendererEnabled: boolean;
  legacyFileFallbackEnabled: boolean;
  browserSandbox: true;
  webSecurity: true;
  inputTransport: "cdp-pipe";
  networkPolicy: "cdp-abort-all";
  productionOsIsolation: "nonroot-readonly-tmpfs-network-none" | "development-unattested";
  executionBoundary: "external-worker" | "development-local";
}

const REQUIRED_PRODUCTION_OS_ISOLATION =
  "nonroot-readonly-tmpfs-network-none" as const;

/** Evaluated for every capture so an operations kill switch takes effect
 * without restarting or silently selecting the legacy file renderer. */
export function authoredRendererIsolationPolicy(): AuthoredRendererIsolationPolicy {
  const production = process.env.NODE_ENV === "production";
  const osIsolation = process.env.AUTHORED_RENDERER_OS_ISOLATION?.trim();
  if (production && osIsolation !== REQUIRED_PRODUCTION_OS_ISOLATION) {
    throw new Error(
      `Production authored rendering requires AUTHORED_RENDERER_OS_ISOLATION=${REQUIRED_PRODUCTION_OS_ISOLATION}. ` +
      "The deployment must run a non-root, read-only worker with tmpfs resource limits and network none."
    );
  }
  const configuredBoundary = process.env.AUTHORED_RENDERER_EXECUTION_BOUNDARY?.trim();
  if (production && configuredBoundary !== "external-worker") {
    throw new Error(
      "Production Next.js must consume artifacts from the external authored renderer worker; direct local Chrome is forbidden."
    );
  }
  const globalKillSwitch = booleanEnvironment("AUTHORED_RENDERER_KILL_SWITCH", false);
  return {
    rendererEnabled:
      !production && !globalKillSwitch && booleanEnvironment("AUTHORED_VNEXT_RENDERER_ENABLED", false),
    legacyFileFallbackEnabled: booleanEnvironment(
      "AUTHORED_LEGACY_FILE_RENDERER_ENABLED",
      false
    ),
    browserSandbox: true,
    webSecurity: true,
    inputTransport: "cdp-pipe",
    networkPolicy: "cdp-abort-all",
    productionOsIsolation: production
      ? REQUIRED_PRODUCTION_OS_ISOLATION
      : "development-unattested",
    executionBoundary: production ? "external-worker" : "development-local",
  };
}

function sha256CspSource(script: string): string {
  return `'sha256-${createHash("sha256").update(script, "utf8").digest("base64")}'`;
}

/**
 * The renderer receives a restrictive document policy in addition to CDP
 * request abortion. Script hashes are calculated after trusted instrumentation
 * and therefore cannot authorize an authored script that failed preflight.
 */
export function applyAuthoredRendererCsp(html: string): string {
  const scriptHashes = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/gi)]
    .filter((match) => !/\btype\s*=\s*["']application\/json["']/i.test(match[0].slice(0, match[0].indexOf(">") + 1)))
    .map((match) => sha256CspSource(match[1]));
  const policy = [
    "default-src 'none'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-src 'none'",
    "child-src 'none'",
    "connect-src 'none'",
    "media-src 'none'",
    "worker-src 'none'",
    "manifest-src 'none'",
    "form-action 'none'",
    "img-src data:",
    "font-src data:",
    "style-src 'unsafe-inline'",
    `script-src ${scriptHashes.length > 0 ? scriptHashes.join(" ") : "'none'"}`,
  ].join("; ");
  const meta = `<meta http-equiv="Content-Security-Policy" content="${policy}">`;
  const withoutAuthoredCsp = html.replace(
    /<meta\b(?=[^>]*http-equiv\s*=\s*["']?content-security-policy["']?)[^>]*>/gi,
    ""
  );
  if (/<head(?:\s[^>]*)?>/i.test(withoutAuthoredCsp)) {
    return withoutAuthoredCsp.replace(
      /<head(?:\s[^>]*)?>/i,
      (opening) => `${opening}${meta}`
    );
  }
  return `<head>${meta}</head>${withoutAuthoredCsp}`;
}

/** Chrome receives operating-system essentials only. Provider credentials and
 * application configuration are deliberately absent from the render worker. */
export function authoredRendererEnvironment(
  source: NodeJS.ProcessEnv = process.env
): NodeJS.ProcessEnv {
  const allowed = [
    "PATH", "PATHEXT", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "LOCALAPPDATA", "LANG", "LC_ALL", "TZ",
  ];
  const environment = { NODE_ENV: "production" } as NodeJS.ProcessEnv;
  for (const name of allowed) {
    if (source[name] !== undefined) environment[name] = source[name];
  }
  return environment;
}

export function authoredRendererChromeArguments(
  profilePath: string,
  windowSize: { width: number; height: number }
): string[] {
  return [
    "--headless=new",
    "--remote-debugging-pipe",
    "--disable-gpu",
    "--hide-scrollbars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-extensions",
    "--disable-features=AutofillServerCommunication,OptimizationHints,MediaRouter",
    "--disable-sync",
    "--metrics-recording-only",
    "--safebrowsing-disable-auto-update",
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=192",
    "--force-device-scale-factor=1",
    `--window-size=${windowSize.width},${windowSize.height}`,
    "--run-all-compositor-stages-before-draw",
    `--user-data-dir=${profilePath}`,
    "about:blank",
  ];
}

/** LKG state is owned by callers. A failed candidate never replaces it. */
export function preserveLastKnownGoodArtifact<T>(
  lastKnownGood: T | undefined,
  candidate: T | undefined
): T | undefined {
  return candidate === undefined ? lastKnownGood : candidate;
}
