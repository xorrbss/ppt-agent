import { spawn, type ChildProcess } from "node:child_process";

const DEFAULT_EXPORT_DEADLINE_MS = 10 * 60 * 1000;
const DEFAULT_TERMINATION_GRACE_MS = 5 * 1000;
const FORCE_EXIT_WAIT_MS = 1_000;

type ExportDeadlineEnvironment = {
  PRESENTATION_EXPORT_DEADLINE_MS?: string;
  PRESENTATION_EXPORT_TERMINATION_GRACE_MS?: string;
};

type SupervisedChild = Pick<
  ChildProcess,
  "pid" | "kill" | "once" | "removeListener"
>;

export class BundledPresentationExportTimeoutError extends Error {
  readonly code = "presentation_export_timeout";
  readonly deadlineMs: number;

  constructor(deadlineMs: number) {
    super(`Presentation export exceeded its ${deadlineMs}ms deadline.`);
    this.name = "BundledPresentationExportTimeoutError";
    this.deadlineMs = deadlineMs;
  }
}

function parsePositiveMilliseconds(
  value: string | undefined,
  fallback: number,
  name: string
): number {
  if (!value?.trim()) return fallback;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer number of milliseconds.`);
  }
  return parsed;
}

export function resolveExportProcessLimits(
  env: NodeJS.ProcessEnv | ExportDeadlineEnvironment = process.env
): { deadlineMs: number; terminationGraceMs: number } {
  return {
    deadlineMs: parsePositiveMilliseconds(
      env.PRESENTATION_EXPORT_DEADLINE_MS,
      DEFAULT_EXPORT_DEADLINE_MS,
      "PRESENTATION_EXPORT_DEADLINE_MS"
    ),
    terminationGraceMs: parsePositiveMilliseconds(
      env.PRESENTATION_EXPORT_TERMINATION_GRACE_MS,
      DEFAULT_TERMINATION_GRACE_MS,
      "PRESENTATION_EXPORT_TERMINATION_GRACE_MS"
    ),
  };
}

function killDirectly(child: SupervisedChild, force: boolean): void {
  try {
    child.kill(force ? "SIGKILL" : "SIGTERM");
  } catch {
    // The child may have exited between the deadline and termination attempt.
  }
}

export async function terminateExportProcessTree(
  child: SupervisedChild,
  force: boolean,
  platform: NodeJS.Platform = process.platform
): Promise<void> {
  const pid = child.pid;
  if (!pid) {
    killDirectly(child, force);
    return;
  }

  if (platform !== "win32") {
    try {
      // The exporter is spawned as its own process group so Chrome descendants
      // receive the same graceful/forced termination signal.
      process.kill(-pid, force ? "SIGKILL" : "SIGTERM");
      return;
    } catch {
      killDirectly(child, force);
      return;
    }
  }

  await new Promise<void>((resolve) => {
    const args = ["/PID", String(pid), "/T"];
    if (force) args.push("/F");
    const taskkill = spawn("taskkill.exe", args, {
      stdio: "ignore",
      windowsHide: true,
    });
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      killDirectly(child, force);
      finish();
    }, FORCE_EXIT_WAIT_MS);
    taskkill.once("error", () => {
      killDirectly(child, force);
      finish();
    });
    taskkill.once("close", finish);
  });
}

export function superviseExportProcess(
  child: SupervisedChild,
  options: {
    deadlineMs: number;
    terminationGraceMs: number;
    terminate?: (child: SupervisedChild, force: boolean) => Promise<void>;
  }
): Promise<{ code: number | null; signal: NodeJS.Signals | null }> {
  const terminate = options.terminate ?? terminateExportProcessTree;

  return new Promise((resolve, reject) => {
    let settled = false;
    let timedOut = false;
    let forceTimer: NodeJS.Timeout | undefined;
    let forceExitTimer: NodeJS.Timeout | undefined;

    const cleanup = () => {
      clearTimeout(deadlineTimer);
      if (forceTimer) clearTimeout(forceTimer);
      if (forceExitTimer) clearTimeout(forceExitTimer);
      child.removeListener("error", onError);
      child.removeListener("close", onClose);
    };
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      cleanup();
      callback();
    };
    const rejectTimeout = () =>
      finish(() =>
        reject(new BundledPresentationExportTimeoutError(options.deadlineMs))
      );
    const onError = (error: Error) => {
      if (timedOut) rejectTimeout();
      else finish(() => reject(error));
    };
    const onClose = (
      code: number | null,
      signal: NodeJS.Signals | null
    ) => {
      if (timedOut) rejectTimeout();
      else finish(() => resolve({ code, signal }));
    };

    const deadlineTimer = setTimeout(() => {
      timedOut = true;
      forceTimer = setTimeout(() => {
        forceExitTimer = setTimeout(rejectTimeout, FORCE_EXIT_WAIT_MS);
        void terminate(child, true).catch(() => {});
      }, options.terminationGraceMs);
      void terminate(child, false).catch(() => {});
    }, options.deadlineMs);

    child.once("error", onError);
    child.once("close", onClose);
  });
}
