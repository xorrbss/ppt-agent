import type { WebPreferences } from "electron";

export function getAppWebPreferences(preloadPath: string): WebPreferences {
  return {
    webSecurity: true,
    allowRunningInsecureContent: false,
    contextIsolation: true,
    nodeIntegration: false,
    // The Electron product path renders authored slides from isolated backend PNGs
    // and never constructs authored srcDoc. Keep both flags explicitly false as a
    // defense-in-depth boundary against preload/IPC reaching any future subframe or
    // worker introduced in the application window.
    nodeIntegrationInSubFrames: false,
    nodeIntegrationInWorker: false,
    sandbox: true,
    webviewTag: false,
    preload: preloadPath,
  };
}

export function getIsolatedMetadataWebPreferences(partition: string): WebPreferences {
  return {
    webSecurity: true,
    allowRunningInsecureContent: false,
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInSubFrames: false,
    nodeIntegrationInWorker: false,
    sandbox: true,
    webviewTag: false,
    partition,
  };
}

export function shouldBlockTrustedMetadataRequest(url: string, allowedOrigin: string): boolean {
  try {
    const parsed = new URL(url);
    return !["http:", "https:"].includes(parsed.protocol) || parsed.origin !== allowedOrigin;
  } catch {
    return true;
  }
}

export function isTrustedIpcSenderUrl(url: string, trustedOrigin: string | undefined): boolean {
  if (!trustedOrigin) return false;
  try {
    const parsed = new URL(url);
    return parsed.origin === trustedOrigin && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}
