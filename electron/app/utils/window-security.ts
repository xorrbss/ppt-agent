import {
  type BrowserWindow,
  type BrowserWindowConstructorOptions,
  type WebContents,
} from "electron";
import {
  classifyNavigationUrl,
  classifyWindowOpenUrl,
  isAnonymousWindowName,
  PopupRequestGuard,
  type InternalNavigationScope,
} from "./navigation-policy";
import { openExternalUrl, showOpenTargetErrorDialog } from "./open-target";
import { safeWarn } from "./safe-console";

interface WindowSecurityControllerOptions {
  getTrustedAppOrigin: () => string | undefined;
  getPresentationWindowOptions: () => BrowserWindowConstructorOptions;
}

interface WindowSecurityContext {
  scope: InternalNavigationScope;
  stagingOwnerId?: number;
}

function focusWindow(window: BrowserWindow): void {
  if (window.isDestroyed()) {
    return;
  }
  if (window.isMinimized()) {
    window.restore();
  }
  window.show();
  window.focus();
}

export function createWindowSecurityController({
  getTrustedAppOrigin,
  getPresentationWindowOptions,
}: WindowSecurityControllerOptions): {
  attach: (window: BrowserWindow, context: WindowSecurityContext) => void;
} {
  const popupRequests = new PopupRequestGuard();
  const presentationWindows = new Map<string, BrowserWindow>();
  const stagingWindows = new Map<number, BrowserWindow>();
  const configuredContents = new WeakSet<WebContents>();

  function getLivePresentationWindow(key: string): BrowserWindow | undefined {
    const existing = presentationWindows.get(key);
    if (!existing || existing.isDestroyed()) {
      presentationWindows.delete(key);
      return undefined;
    }
    return existing;
  }

  function getLiveStagingWindow(ownerId: number): BrowserWindow | undefined {
    const existing = stagingWindows.get(ownerId);
    if (!existing || existing.isDestroyed()) {
      stagingWindows.delete(ownerId);
      return undefined;
    }
    return existing;
  }

  function removeStagingWindow(
    ownerId: number | undefined,
    window: BrowserWindow,
  ): void {
    if (ownerId !== undefined && stagingWindows.get(ownerId) === window) {
      stagingWindows.delete(ownerId);
    }
  }

  function handleExternalUrl(url: string, parent: BrowserWindow): void {
    void openExternalUrl(url)
      .then(async (result) => {
        if (result.success) {
          return;
        }

        safeWarn(
          `[Presenton] Failed to open external URL: ${result.message || "Unknown error"}`,
        );
        await showOpenTargetErrorDialog({
          parent,
          title: "Could Not Open Link",
          message: "Presenton could not open this link in your browser.",
          detail: `${result.message || "No application is registered to open this link."}\n\n${url}`,
        });
      })
      .catch((error) => {
        safeWarn("[Presenton] Failed to handle external URL open:", error);
      });
  }

  function attachNavigationPolicy(
    window: BrowserWindow,
    context: WindowSecurityContext,
  ): void {
    const handleNavigation = (
      event: Electron.Event<Electron.WebContentsWillNavigateEventParams>,
    ): void => {
      const disposition = classifyNavigationUrl(
        event.url,
        getTrustedAppOrigin(),
        context.scope,
      );
      if (disposition === "internal") {
        if (context.scope === "presentation-staging") {
          const workspace = classifyWindowOpenUrl(
            event.url,
            getTrustedAppOrigin(),
          );
          if (workspace.action !== "internal-presentation") {
            event.preventDefault();
            return;
          }

          const existing = getLivePresentationWindow(workspace.key);
          if (existing && existing !== window) {
            event.preventDefault();
            removeStagingWindow(context.stagingOwnerId, window);
            window.destroy();
            focusWindow(existing);
            return;
          }

          removeStagingWindow(context.stagingOwnerId, window);
          presentationWindows.set(workspace.key, window);
          context.scope = "presentation";
          window.once("closed", () => {
            if (presentationWindows.get(workspace.key) === window) {
              presentationWindows.delete(workspace.key);
            }
          });
        }
        return;
      }

      event.preventDefault();
      if (disposition === "external" && event.isMainFrame) {
        handleExternalUrl(event.url, window);
        return;
      }

      safeWarn("[Presenton] Blocked untrusted in-app navigation.");
    };

    window.webContents.on("will-navigate", handleNavigation);
    window.webContents.on("will-redirect", handleNavigation);
  }

  function attachWindowOpenPolicy(
    window: BrowserWindow,
    context: WindowSecurityContext,
  ): void {
    window.webContents.setWindowOpenHandler(({ url, frameName }) => {
      if (context.scope === "presentation-staging") {
        safeWarn("[Presenton] Blocked popup from a staging workspace window.");
        return { action: "deny" };
      }

      const disposition = classifyWindowOpenUrl(url, getTrustedAppOrigin());
      if (disposition.action === "deny") {
        safeWarn("[Presenton] Blocked unsupported window open URL.");
        return { action: "deny" };
      }

      if (disposition.action === "external") {
        handleExternalUrl(url, window);
        return { action: "deny" };
      }

      if (disposition.action === "internal-presentation-staging") {
        if (!isAnonymousWindowName(frameName)) {
          safeWarn("[Presenton] Blocked invalid presentation staging window.");
          return { action: "deny" };
        }

        const ownerId = window.webContents.id;
        const existing = getLiveStagingWindow(ownerId);
        if (existing) {
          focusWindow(existing);
          safeWarn("[Presenton] Blocked duplicate presentation staging window.");
          return { action: "deny" };
        }

        const reservationKey = `staging:${ownerId}`;
        if (!popupRequests.reserve(reservationKey)) {
          safeWarn("[Presenton] Blocked repeated presentation staging request.");
          return { action: "deny" };
        }

        try {
          return {
            action: "allow" as const,
            outlivesOpener: true,
            overrideBrowserWindowOptions: getPresentationWindowOptions(),
          };
        } catch (error) {
          popupRequests.release(reservationKey);
          safeWarn("[Presenton] Failed to prepare staging window:", error);
          return { action: "deny" };
        }
      }

      const existing = getLivePresentationWindow(disposition.key);
      if (existing) {
        focusWindow(existing);
        safeWarn("[Presenton] Blocked duplicate presentation workspace window.");
        return { action: "deny" };
      }

      if (!popupRequests.reserve(disposition.key)) {
        safeWarn("[Presenton] Blocked repeated presentation workspace request.");
        return { action: "deny" };
      }

      try {
        // Returning Electron's native `allow` response is intentional: renderer
        // window.open receives null when creation is denied or fails, preserving
        // its same-tab fallback instead of hiding failures behind a manual window.
        return {
          action: "allow" as const,
          outlivesOpener: true,
          overrideBrowserWindowOptions: getPresentationWindowOptions(),
        };
      } catch (error) {
        popupRequests.release(disposition.key);
        safeWarn("[Presenton] Failed to prepare presentation workspace window:", error);
        return { action: "deny" };
      }
    });

    window.webContents.on("did-create-window", (childWindow, details) => {
      const disposition = classifyWindowOpenUrl(
        details.url,
        getTrustedAppOrigin(),
      );
      if (disposition.action === "internal-presentation-staging") {
        const ownerId = window.webContents.id;
        popupRequests.release(`staging:${ownerId}`);
        const existing = getLiveStagingWindow(ownerId);
        if (existing && existing !== childWindow) {
          childWindow.destroy();
          focusWindow(existing);
          return;
        }

        stagingWindows.set(ownerId, childWindow);
        childWindow.once("closed", () => {
          removeStagingWindow(ownerId, childWindow);
        });
        attach(childWindow, {
          scope: "presentation-staging",
          stagingOwnerId: ownerId,
        });
        // about:blank can become ready before did-create-window observers run.
        // Show it immediately so the renderer can paint its progress message.
        focusWindow(childWindow);
        return;
      }

      if (disposition.action !== "internal-presentation") {
        safeWarn("[Presenton] Destroyed an unexpected internal child window.");
        childWindow.destroy();
        return;
      }

      popupRequests.release(disposition.key);
      const existing = getLivePresentationWindow(disposition.key);
      if (existing && existing !== childWindow) {
        childWindow.destroy();
        focusWindow(existing);
        return;
      }

      presentationWindows.set(disposition.key, childWindow);
      childWindow.once("closed", () => {
        if (presentationWindows.get(disposition.key) === childWindow) {
          presentationWindows.delete(disposition.key);
        }
      });
      childWindow.once("ready-to-show", () => focusWindow(childWindow));
      attach(childWindow, { scope: "presentation" });
    });
  }

  function attach(
    window: BrowserWindow,
    context: WindowSecurityContext,
  ): void {
    if (configuredContents.has(window.webContents)) {
      return;
    }
    configuredContents.add(window.webContents);
    attachNavigationPolicy(window, context);
    attachWindowOpenPolicy(window, context);
  }

  return { attach };
}
