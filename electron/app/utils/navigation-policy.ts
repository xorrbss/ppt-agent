const WEB_PROTOCOLS = new Set(["http:", "https:"]);
const PRESENTATION_PATHS = new Set(["/presentation", "/presentation/"]);
const MAX_PRESENTATION_ID_LENGTH = 256;

export type WindowOpenDisposition =
  | {
      action: "internal-presentation";
      key: string;
      url: string;
    }
  | { action: "internal-presentation-staging" }
  | { action: "external" }
  | { action: "deny" };

export type NavigationDisposition = "internal" | "external" | "deny";
export type InternalNavigationScope =
  | "app"
  | "presentation"
  | "presentation-staging";

function parseWebUrl(candidate: string): URL | null {
  try {
    const url = new URL(candidate);
    if (!WEB_PROTOCOLS.has(url.protocol)) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

function parseTrustedOrigin(candidate: string | undefined): string | null {
  if (!candidate) {
    return null;
  }

  const url = parseWebUrl(candidate);
  if (
    !url ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    return null;
  }
  return url.origin;
}

export function isSupportedWebUrl(candidate: string): boolean {
  return parseWebUrl(candidate) !== null;
}

export function isAnonymousWindowName(frameName: string): boolean {
  // Chromium reports target="_blank" as either "_blank" or an empty frame
  // name depending on the window-open path. Both mean an unnamed auxiliary
  // window; explicit reusable names remain blocked for the staging placeholder.
  return frameName === "" || frameName === "_blank";
}

export function isTrustedAppUrl(
  candidate: string,
  trustedAppOrigin: string | undefined,
): boolean {
  const url = parseWebUrl(candidate);
  const trustedOrigin = parseTrustedOrigin(trustedAppOrigin);
  return Boolean(
    url &&
      !url.username &&
      !url.password &&
      trustedOrigin &&
      url.origin === trustedOrigin,
  );
}

export function getTrustedPresentationWorkspace(
  candidate: string,
  trustedAppOrigin: string | undefined,
): { key: string; url: string } | null {
  const url = parseWebUrl(candidate);
  const trustedOrigin = parseTrustedOrigin(trustedAppOrigin);
  if (
    !url ||
    url.username ||
    url.password ||
    !trustedOrigin ||
    url.origin !== trustedOrigin ||
    !PRESENTATION_PATHS.has(url.pathname)
  ) {
    return null;
  }

  const presentationId = url.searchParams.get("id")?.trim();
  if (
    !presentationId ||
    presentationId.length > MAX_PRESENTATION_ID_LENGTH
  ) {
    return null;
  }

  return {
    // One workspace window per presentation, independent of transient view params.
    key: `${trustedOrigin}/presentation?id=${encodeURIComponent(presentationId)}`,
    url: url.toString(),
  };
}

export function classifyWindowOpenUrl(
  candidate: string,
  trustedAppOrigin: string | undefined,
): WindowOpenDisposition {
  const workspace = getTrustedPresentationWorkspace(candidate, trustedAppOrigin);
  if (workspace) {
    return {
      action: "internal-presentation",
      ...workspace,
    };
  }

  // The web flow opens an inert synchronous placeholder during the user
  // gesture, then replaces it with the async-created presentation URL.
  if (candidate === "about:blank" && parseTrustedOrigin(trustedAppOrigin)) {
    return { action: "internal-presentation-staging" };
  }

  return isSupportedWebUrl(candidate)
    ? { action: "external" }
    : { action: "deny" };
}

export function classifyNavigationUrl(
  candidate: string,
  trustedAppOrigin: string | undefined,
  scope: InternalNavigationScope,
): NavigationDisposition {
  if (isTrustedAppUrl(candidate, trustedAppOrigin)) {
    if (scope === "app") {
      return "internal";
    }
    return getTrustedPresentationWorkspace(candidate, trustedAppOrigin)
      ? "internal"
      : "deny";
  }

  return isSupportedWebUrl(candidate) ? "external" : "deny";
}

export class PopupRequestGuard {
  private readonly reservations = new Map<string, number>();

  constructor(
    private readonly ttlMs = 5_000,
    private readonly now: () => number = Date.now,
  ) {}

  reserve(key: string): boolean {
    const timestamp = this.now();
    for (const [reservedKey, expiry] of this.reservations) {
      if (expiry <= timestamp) {
        this.reservations.delete(reservedKey);
      }
    }

    const existingExpiry = this.reservations.get(key);
    if (existingExpiry !== undefined && existingExpiry > timestamp) {
      return false;
    }

    this.reservations.set(key, timestamp + this.ttlMs);
    return true;
  }

  release(key: string): void {
    this.reservations.delete(key);
  }
}
