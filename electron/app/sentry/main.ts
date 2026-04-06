import { app } from "electron";
import * as Sentry from "@sentry/electron/main";

let isSentryInitialized = false;

function parseBoolean(value: string | undefined, defaultValue: boolean): boolean {
  if (value === undefined) {
    return defaultValue;
  }

  return !["0", "false", "no", "off"].includes(value.trim().toLowerCase());
}

function parseSampleRate(value: string | undefined, defaultValue: number): number {
  if (!value) {
    return defaultValue;
  }

  const parsed = Number.parseFloat(value);
  if (Number.isNaN(parsed)) {
    return defaultValue;
  }

  return Math.max(0, Math.min(1, parsed));
}

function getEnvironment(): string {
  if (process.env.SENTRY_ENVIRONMENT) {
    return process.env.SENTRY_ENVIRONMENT;
  }

  if (process.env.NODE_ENV) {
    return process.env.NODE_ENV;
  }

  return app.isPackaged ? "production" : "development";
}

function getRelease(): string {
  if (process.env.SENTRY_RELEASE) {
    return process.env.SENTRY_RELEASE;
  }

  return `presenton-electron@${app.getVersion()}`;
}

export function initMainSentry(): void {
  if (isSentryInitialized) {
    return;
  }

  const dsn = "https://48b091ed88ae147c0957a46a823c1449@o4509882707410944.ingest.us.sentry.io/4511171070394368";
  const isEnabled = parseBoolean(process.env.SENTRY_ENABLED, true);

  if (!isEnabled) {
    return;
  }

  const tracesSampleRate = parseSampleRate(
    process.env.SENTRY_TRACES_SAMPLE_RATE,
    app.isPackaged ? 0.2 : 1.0,
  );

  try {
    Sentry.init({
      dsn,
      enabled: true,
      release: getRelease(),
      environment: getEnvironment(),
      debug: parseBoolean(process.env.SENTRY_DEBUG, false),
      sendDefaultPii: parseBoolean(process.env.SENTRY_SEND_DEFAULT_PII, false),
      enableLogs: parseBoolean(process.env.SENTRY_ENABLE_LOGS, true),
      tracesSampleRate,
      integrations: [Sentry.startupTracingIntegration()],
    });

    isSentryInitialized = true;
    Sentry.setTag("process.type", "main");
    console.log("[Sentry] Initialized in Electron main process.");
  } catch (error) {
    console.error("[Sentry] Failed to initialize in Electron main process:", error);
  }
}

export function captureMainSentryTestError(message?: string): string | null {
  if (!isSentryInitialized) {
    return null;
  }

  const error = new Error(message || "Sentry test error in main process");
  return Sentry.captureException(error);
}
