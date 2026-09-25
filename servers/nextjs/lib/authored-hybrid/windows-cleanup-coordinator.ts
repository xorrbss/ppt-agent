/**
 * Shared, bounded coordination for the Windows Chrome cleanup helpers.
 *
 * Teardown used to run its own full process-table scan per capture — four kill
 * attempts plus a verification pass, each one a `Get-CimInstance Win32_Process`
 * PowerShell process. Authored export admits twelve concurrent captures, so a
 * single teardown wave could put roughly sixty process-table scans plus every
 * `taskkill` on the host at once, and each helper was then measured against the
 * same fixed timeout it was itself starving. The observed result was
 * `AggregateError: Windows Chrome could not be fully terminated.` on every
 * slide of an isolated export, carrying `Windows cleanup helper … timed out
 * after 10000ms` underneath.
 *
 * This coordinator removes the contention rather than widening the budget:
 *
 * - the process-table scan is **single-flight and shared**, so concurrent
 *   teardowns read one scan instead of one each, and
 * - every helper runs through a **small bounded queue**, so the helper timeout
 *   is spent on a helper that is actually running.
 *
 * Freshness is explicit, not assumed. A caller that has just issued kills must
 * observe a scan that started afterwards, so it passes `notBefore`; a scan that
 * predates it is waited out rather than reused, and every waiter in that
 * position coalesces onto the next scan.
 *
 * This owns no process lifetime. Job Object ownership of the Chrome tree
 * already exists elsewhere and is deliberately not duplicated here.
 */

/** One `--user-data-dir` occurrence observed on one process. */
export interface WindowsProfileProcess {
  readonly pid: number;
  readonly userDataDir: string;
}

export interface WindowsProfileScan {
  /** Wall clock at which this scan began, for `notBefore` comparisons. */
  readonly startedAt: number;
  readonly processes: readonly WindowsProfileProcess[];
}

export interface WindowsCleanupTelemetry {
  readonly scansStarted: number;
  readonly scansJoined: number;
  readonly scansServedFromCache: number;
  readonly peakScansInFlight: number;
  readonly peakHelpersInFlight: number;
  readonly peakHelperQueueDepth: number;
}

export interface WindowsCleanupCoordinatorOptions {
  /** Enumerate every process carrying a `--user-data-dir` argument. */
  readonly scan: () => Promise<readonly WindowsProfileProcess[]>;
  /** Helpers allowed to run at once. Small on purpose. */
  readonly helperConcurrency?: number;
  /** How long a completed scan may answer a caller that accepts its start. */
  readonly cacheTtlMs?: number;
  readonly now?: () => number;
}

const DEFAULT_HELPER_CONCURRENCY = 4;
const DEFAULT_CACHE_TTL_MS = 500;
/** A caller waits out at most this many scans before its freshness is refused. */
const MAX_SCAN_GENERATIONS = 8;

interface InFlightScan {
  readonly id: number;
  readonly startedAt: number;
  readonly promise: Promise<WindowsProfileScan>;
}

export class WindowsCleanupCoordinator {
  private readonly scan: () => Promise<readonly WindowsProfileProcess[]>;
  private readonly helperConcurrency: number;
  private readonly cacheTtlMs: number;
  private readonly now: () => number;

  private inFlight: InFlightScan | null = null;
  private cached: WindowsProfileScan | null = null;
  private scanSequence = 0;
  private scansInFlight = 0;
  private helpersInFlight = 0;
  private readonly waiting: Array<() => void> = [];

  private scansStarted = 0;
  private scansJoined = 0;
  private scansServedFromCache = 0;
  private peakScansInFlight = 0;
  private peakHelpersInFlight = 0;
  private peakHelperQueueDepth = 0;

  constructor(options: WindowsCleanupCoordinatorOptions) {
    this.scan = options.scan;
    this.helperConcurrency = Math.max(1, options.helperConcurrency ?? DEFAULT_HELPER_CONCURRENCY);
    this.cacheTtlMs = Math.max(0, options.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS);
    this.now = options.now ?? Date.now;
  }

  /** Process IDs whose `--user-data-dir` is exactly `userDataDir`.
   *
   * The comparison stays byte-exact — the PowerShell form it replaces used
   * `-ceq`, and a case-insensitive match here would let a sibling profile
   * differing only in case be killed as if it were this capture's own. */
  async profileProcesses(
    userDataDir: string,
    options: { notBefore?: number } = {},
  ): Promise<number[]> {
    const scan = await this.requestScan(options.notBefore ?? 0);
    const pids = new Set<number>();
    for (const observed of scan.processes) {
      if (observed.userDataDir === userDataDir) pids.add(observed.pid);
    }
    return [...pids];
  }

  /** Run one cleanup helper once a queue slot is free. */
  async runHelper<T>(task: () => Promise<T>): Promise<T> {
    await this.acquireHelperSlot();
    try {
      return await task();
    } finally {
      this.releaseHelperSlot();
    }
  }

  telemetry(): WindowsCleanupTelemetry {
    return {
      scansStarted: this.scansStarted,
      scansJoined: this.scansJoined,
      scansServedFromCache: this.scansServedFromCache,
      peakScansInFlight: this.peakScansInFlight,
      peakHelpersInFlight: this.peakHelpersInFlight,
      peakHelperQueueDepth: this.peakHelperQueueDepth,
    };
  }

  /** Drop counters and any cached scan. Test and diagnostic use only. */
  resetForTesting(): void {
    this.cached = null;
    this.scansStarted = 0;
    this.scansJoined = 0;
    this.scansServedFromCache = 0;
    this.peakScansInFlight = 0;
    this.peakHelpersInFlight = 0;
    this.peakHelperQueueDepth = 0;
  }

  private async requestScan(notBefore: number): Promise<WindowsProfileScan> {
    for (let generation = 0; generation < MAX_SCAN_GENERATIONS; generation += 1) {
      const cached = this.cached;
      if (
        cached !== null &&
        cached.startedAt >= notBefore &&
        this.now() - cached.startedAt <= this.cacheTtlMs
      ) {
        this.scansServedFromCache += 1;
        return cached;
      }
      const inFlight = this.inFlight;
      if (inFlight === null) return this.startScan();
      if (inFlight.startedAt >= notBefore) {
        this.scansJoined += 1;
        return inFlight.promise;
      }
      // The running scan is older than what this caller must observe, which
      // normally means the caller just issued kills. Wait it out and take the
      // next generation; a failed scan is not this caller's failure to report.
      await inFlight.promise.then(
        () => undefined,
        () => undefined,
      );
    }
    throw new Error(
      "Windows cleanup could not observe a process table newer than its own kills.",
    );
  }

  private startScan(): Promise<WindowsProfileScan> {
    const id = (this.scanSequence += 1);
    const startedAt = this.now();
    this.scansStarted += 1;
    this.scansInFlight += 1;
    this.peakScansInFlight = Math.max(this.peakScansInFlight, this.scansInFlight);
    // Deferred to a microtask so `inFlight` is always registered before the
    // settlement handler can try to clear it — including when `scan` throws
    // synchronously, which would otherwise leave a rejected entry installed
    // and every later caller joining it forever.
    const promise = Promise.resolve()
      .then(() => this.scan())
      .then((processes) => {
        const scan: WindowsProfileScan = { startedAt, processes };
        this.cached = scan;
        return scan;
      })
      .finally(() => {
        this.scansInFlight -= 1;
        if (this.inFlight?.id === id) this.inFlight = null;
      });
    this.inFlight = { id, startedAt, promise };
    return promise;
  }

  private acquireHelperSlot(): Promise<void> {
    if (this.helpersInFlight < this.helperConcurrency) {
      this.helpersInFlight += 1;
      this.peakHelpersInFlight = Math.max(this.peakHelpersInFlight, this.helpersInFlight);
      return Promise.resolve();
    }
    this.peakHelperQueueDepth = Math.max(this.peakHelperQueueDepth, this.waiting.length + 1);
    return new Promise<void>((resolve) => {
      this.waiting.push(resolve);
    });
  }

  private releaseHelperSlot(): void {
    const next = this.waiting.shift();
    if (next !== undefined) {
      // Hand the slot straight over. Decrementing first would let a caller
      // arriving in the same tick take it and push the queue past its bound.
      next();
      return;
    }
    this.helpersInFlight -= 1;
  }
}

/** Parse the shared scan's `<pid>\t<user-data-dir>` lines. */
export function parseWindowsProfileScan(output: string): WindowsProfileProcess[] {
  const processes: WindowsProfileProcess[] = [];
  for (const line of output.split(/\r?\n/)) {
    const separator = line.indexOf("\t");
    if (separator <= 0) continue;
    const pid = Number(line.slice(0, separator).trim());
    // A directory may contain spaces, so only the first tab is a separator and
    // the remainder is taken verbatim.
    const userDataDir = line.slice(separator + 1);
    if (!Number.isSafeInteger(pid) || pid <= 0 || userDataDir === "") continue;
    processes.push({ pid, userDataDir });
  }
  return processes;
}
