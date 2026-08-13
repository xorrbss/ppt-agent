import crypto from "crypto";
import {
  BrowserWindow,
  type IpcMainInvokeEvent,
  type Session,
} from "electron";
import {
  getIsolatedMetadataWebPreferences,
  isTrustedIpcSenderUrl,
  shouldBlockTrustedMetadataRequest,
} from "./authored-renderer-policy";

function createEphemeralPartition(scope: string): string {
  return `${scope}-${crypto.randomUUID()}`;
}

function denySessionCapabilities(renderSession: Session): void {
  renderSession.setPermissionCheckHandler(() => false);
  renderSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  renderSession.setDevicePermissionHandler(() => false);
  renderSession.setDisplayMediaRequestHandler((_request, callback) => {
    callback({});
  });
  renderSession.setSpellCheckerEnabled(false);
  renderSession.on("will-download", (event) => {
    event.preventDefault();
  });
}

function denyWindowCapabilities(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => {
    event.preventDefault();
  });
  window.webContents.on("will-redirect", (event) => {
    event.preventDefault();
  });
  window.webContents.on("will-attach-webview", (event) => {
    event.preventDefault();
  });
}

function createHiddenMetadataWindow(partition: string): BrowserWindow {
  const window = new BrowserWindow({
    width: 1280,
    height: 720,
    show: false,
    paintWhenInitiallyHidden: true,
    webPreferences: getIsolatedMetadataWebPreferences(partition),
  });
  denySessionCapabilities(window.webContents.session);
  denyWindowCapabilities(window);
  return window;
}

export function createTrustedMetadataWindow(allowedOrigin: string): BrowserWindow {
  const window = createHiddenMetadataWindow(createEphemeralPartition("slide-metadata"));
  window.webContents.session.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: shouldBlockTrustedMetadataRequest(details.url, allowedOrigin) });
  });
  return window;
}

export function assertTrustedIpcSender(
  event: IpcMainInvokeEvent,
  trustedOrigin: string | undefined,
): void {
  if (!event.senderFrame || event.senderFrame !== event.sender.mainFrame) {
    throw new Error("Blocked IPC invocation from a subframe renderer.");
  }
  const senderUrl = event.senderFrame.url;
  if (!isTrustedIpcSenderUrl(senderUrl, trustedOrigin)) {
    throw new Error("Blocked IPC invocation from an untrusted renderer.");
  }
}

export function assertTrustedMetadataUrl(url: string, trustedOrigin: string | undefined): URL {
  if (!trustedOrigin) {
    throw new Error("The trusted application origin is not ready.");
  }
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error("Slide metadata URL is invalid.");
  }
  if (
    parsed.origin !== trustedOrigin ||
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username || parsed.password
  ) {
    throw new Error("Slide metadata URL must use the trusted application origin.");
  }
  return parsed;
}
