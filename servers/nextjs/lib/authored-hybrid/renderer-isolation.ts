import { createHash } from "node:crypto";

const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);
const FALSE_VALUES = new Set(["0", "false", "no", "off"]);

function booleanEnvironment(name: string, fallback: boolean, source: NodeJS.ProcessEnv = process.env): boolean {
  const value = source[name]?.trim().toLowerCase();
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
  productionOsIsolation:
    | "nonroot-readonly-tmpfs-network-none"
    | "development-unattested"
    | "local-native-unattested";
  executionBoundary: "external-worker" | "development-local" | "local-native";
}

export interface AuthoredRendererAttestationProjection {
  schemaVersion: "authored-renderer-attestation/v1";
  state: "unattested" | "attested" | "expired" | "blocked";
  reasonCode: string;
  /** Opaque receipt reference only; never a credential or renderer command. */
  attestationDigest: string | null;
  environment: {
    nodeEnvironment: "production" | "development";
    osIsolation: string;
    executionBoundary: string;
  };
  expiresAt: string | null;
}

const REQUIRED_PRODUCTION_OS_ISOLATION =
  "nonroot-readonly-tmpfs-network-none" as const;

/**
 * The single-user local native profile. `RUNNING-LOCAL.md` builds and serves this
 * fork with `npm run build; npm run start`, which is `NODE_ENV=production` on a
 * Windows desktop where no container can supply the non-root read-only tmpfs
 * worker — so that flow has no honest attestation to make.
 *
 * It is spelled as an explicit *non*-attestation rather than a boolean escape
 * hatch, and it must be declared twice (isolation value plus execution boundary)
 * so a real deployment cannot drift into it by setting one stray variable.
 * Everything it renders records `local-native-unattested`, keeping its artifacts
 * distinguishable from isolated-worker renders instead of forging the guarantee.
 */
const LOCAL_NATIVE_OS_ISOLATION = "local-native-unattested" as const;
const LOCAL_NATIVE_BOUNDARY = "local-native" as const;

/** Evaluated for every capture so an operations kill switch takes effect
 * without restarting or silently selecting the legacy file renderer. */
export function authoredRendererIsolationPolicy(environment: NodeJS.ProcessEnv = process.env): AuthoredRendererIsolationPolicy {
  const production = environment.NODE_ENV === "production";
  const osIsolation = environment.AUTHORED_RENDERER_OS_ISOLATION?.trim();
  const configuredBoundary = environment.AUTHORED_RENDERER_EXECUTION_BOUNDARY?.trim();
  const localNative = production && osIsolation === LOCAL_NATIVE_OS_ISOLATION;
  if (production && !localNative && osIsolation !== REQUIRED_PRODUCTION_OS_ISOLATION) {
    throw new Error(
      `Production authored rendering requires AUTHORED_RENDERER_OS_ISOLATION=${REQUIRED_PRODUCTION_OS_ISOLATION}. ` +
      "The deployment must run a non-root, read-only worker with tmpfs resource limits and network none. " +
      `A single-user local native run may instead declare AUTHORED_RENDERER_OS_ISOLATION=${LOCAL_NATIVE_OS_ISOLATION} ` +
      `with AUTHORED_RENDERER_EXECUTION_BOUNDARY=${LOCAL_NATIVE_BOUNDARY}.`
    );
  }
  if (localNative && configuredBoundary !== LOCAL_NATIVE_BOUNDARY) {
    throw new Error(
      `The unattested local profile requires AUTHORED_RENDERER_EXECUTION_BOUNDARY=${LOCAL_NATIVE_BOUNDARY}. ` +
      "Declaring the profile once must never be enough to run local Chrome where an isolated worker is expected."
    );
  }
  if (production && !localNative && configuredBoundary !== "external-worker") {
    throw new Error(
      "Production Next.js must consume artifacts from the external authored renderer worker; direct local Chrome is forbidden."
    );
  }
  // Only an attested production deployment is barred from driving Chrome itself.
  const externalWorker = production && !localNative;
  const globalKillSwitch = booleanEnvironment("AUTHORED_RENDERER_KILL_SWITCH", false, environment);
  return {
    rendererEnabled:
      !externalWorker && !globalKillSwitch && booleanEnvironment("AUTHORED_VNEXT_RENDERER_ENABLED", false, environment),
    legacyFileFallbackEnabled: booleanEnvironment(
      "AUTHORED_LEGACY_FILE_RENDERER_ENABLED", false, environment
    ),
    browserSandbox: true,
    webSecurity: true,
    inputTransport: "cdp-pipe",
    networkPolicy: "cdp-abort-all",
    productionOsIsolation: localNative
      ? LOCAL_NATIVE_OS_ISOLATION
      : production
        ? REQUIRED_PRODUCTION_OS_ISOLATION
        : "development-unattested",
    executionBoundary: localNative
      ? LOCAL_NATIVE_BOUNDARY
      : production
        ? "external-worker"
        : "development-local",
  };
}

function optionalAttestationDigest(value: string | undefined): string | null {
  const normalized = value?.trim().toLowerCase();
  return normalized && /^sha256:[a-f0-9]{64}$/.test(normalized) ? normalized : null;
}

/**
 * Operational, server-side projection for the v2 release-readiness endpoint.
 * It reports an environment claim but never makes a Next.js browser process an
 * attestation issuer or changes renderer authorization.
 */
export function authoredRendererAttestationProjection(
  environment: NodeJS.ProcessEnv = process.env,
  now = Date.now(),
): AuthoredRendererAttestationProjection {
  const nodeEnvironment: AuthoredRendererAttestationProjection["environment"]["nodeEnvironment"] =
    environment.NODE_ENV === "production" ? "production" : "development";
  const osIsolation = environment.AUTHORED_RENDERER_OS_ISOLATION?.trim() || "undeclared";
  const executionBoundary = environment.AUTHORED_RENDERER_EXECUTION_BOUNDARY?.trim() || "undeclared";
  const base: Omit<AuthoredRendererAttestationProjection, "state" | "reasonCode"> = {
    schemaVersion: "authored-renderer-attestation/v1" as const,
    attestationDigest: optionalAttestationDigest(environment.AUTHORED_RENDERER_ATTESTATION_DIGEST),
    environment: { nodeEnvironment, osIsolation, executionBoundary },
    expiresAt: environment.AUTHORED_RENDERER_ATTESTATION_EXPIRES_AT?.trim() || null,
  };
  if (booleanEnvironment("AUTHORED_RENDERER_KILL_SWITCH", false, environment)) {
    return { ...base, state: "blocked", reasonCode: "authored_renderer_kill_switch_active" };
  }
  try {
    authoredRendererIsolationPolicy(environment);
  } catch {
    return { ...base, state: "blocked", reasonCode: "renderer_isolation_policy_invalid" };
  }
  const expiresAt = base.expiresAt ? Date.parse(base.expiresAt) : Number.NaN;
  if (base.expiresAt && (!Number.isFinite(expiresAt) || expiresAt <= now)) {
    return { ...base, state: "expired", reasonCode: "renderer_attestation_expired" };
  }
  if (
    nodeEnvironment === "production" &&
    osIsolation === REQUIRED_PRODUCTION_OS_ISOLATION &&
    executionBoundary === "external-worker" &&
    base.attestationDigest &&
    base.expiresAt
  ) {
    return { ...base, state: "attested", reasonCode: "renderer_attestation_reported" };
  }
  return { ...base, state: "unattested", reasonCode: "renderer_attestation_missing" };
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
