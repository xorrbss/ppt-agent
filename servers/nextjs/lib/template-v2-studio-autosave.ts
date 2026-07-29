export type TemplateV2AutosaveTrigger =
  | "debounce"
  | "flush"
  | "queued"
  | "dispose";

export interface TemplateV2AutosaveContext {
  /**
   * Describes why this attempt started. A lifecycle save should use transport
   * options suitable for navigation (for example `fetch(..., { keepalive:
   * true })`) instead of an AbortController owned by the mounted editor.
   */
  trigger: TemplateV2AutosaveTrigger;
}

export interface TemplateV2AutosaveTimers {
  setTimeout(callback: () => void, delayMs: number): unknown;
  clearTimeout(handle: unknown): void;
}

export interface TemplateV2AutosaveSchedulerOptions<TSnapshot, TResult> {
  debounceMs: number;
  save(
    snapshot: TSnapshot,
    context: TemplateV2AutosaveContext
  ): Promise<TResult>;
  onSaved?(
    snapshot: TSnapshot,
    result: TResult,
    context: TemplateV2AutosaveContext
  ): void | Promise<void>;
  /**
   * HTTP and domain errors are deliberately opaque here. The caller owns
   * classification of the `/api/v1` revision-conflict response and chooses
   * whether to reload, explicitly retry, or discard the pending snapshot.
   */
  onError?(
    error: unknown,
    snapshot: TSnapshot,
    context: TemplateV2AutosaveContext
  ): void;
  timers?: TemplateV2AutosaveTimers;
}

export interface TemplateV2AutosaveSchedulerState {
  pending: boolean;
  inFlight: boolean;
  blockedByError: boolean;
  closing: boolean;
  disposed: boolean;
}

export interface TemplateV2AutosaveDisposeOptions {
  /**
   * Defaults to true. Setting this to false intentionally abandons a debounced
   * snapshot, but an already-started request is still awaited and never
   * aborted by the scheduler.
   */
  flush?: boolean;
}

export interface TemplateV2AutosaveScheduler<TSnapshot> {
  /**
   * Replaces the pending immutable snapshot and restarts the debounce window.
   * Returns false after disposal has begun.
   */
  schedule(snapshot: TSnapshot): boolean;
  /**
   * Explicitly drains the newest snapshot, waiting for any current save and
   * any edit queued during it. Rejects when a save or onSaved callback fails.
   */
  flush(): Promise<void>;
  /**
   * Re-enables debounced saving after the caller has handled an error or
   * revision conflict. Failures never schedule their own retry.
   */
  resume(): void;
  /**
   * Drops a retained snapshot, normally after an explicit conflict reload.
   */
  discardPending(): void;
  /**
   * Stops accepting edits. By default it flushes before resolving; it never
   * aborts an in-flight save.
   */
  dispose(options?: TemplateV2AutosaveDisposeOptions): Promise<void>;
  getState(): TemplateV2AutosaveSchedulerState;
}

const DEFAULT_TIMERS: TemplateV2AutosaveTimers = {
  setTimeout(callback, delayMs) {
    return globalThis.setTimeout(callback, delayMs);
  },
  clearTimeout(handle) {
    globalThis.clearTimeout(handle as ReturnType<typeof globalThis.setTimeout>);
  },
};

export function createTemplateV2AutosaveScheduler<TSnapshot, TResult>(
  options: TemplateV2AutosaveSchedulerOptions<TSnapshot, TResult>
): TemplateV2AutosaveScheduler<TSnapshot> {
  if (!Number.isFinite(options.debounceMs) || options.debounceMs < 0) {
    throw new RangeError("debounceMs must be a finite non-negative number");
  }

  const timers = options.timers ?? DEFAULT_TIMERS;
  let timerHandle: unknown;
  let timerArmed = false;
  let pendingSnapshot: TSnapshot;
  let hasPendingSnapshot = false;
  let ready = false;
  let blockedByError = false;
  let accepting = true;
  let closing = false;
  let disposeShouldFlush = false;
  let disposed = false;
  let inFlight: Promise<void> | null = null;
  let inFlightSnapshot: TSnapshot | undefined;
  let disposePromise: Promise<void> | null = null;

  function clearDebounce(): void {
    if (!timerArmed) return;
    timers.clearTimeout(timerHandle);
    timerArmed = false;
    timerHandle = undefined;
  }

  function reportError(
    error: unknown,
    snapshot: TSnapshot,
    context: TemplateV2AutosaveContext
  ): void {
    try {
      options.onError?.(error, snapshot, context);
    } catch {
      // An observer must not replace the persistence error seen by flush().
    }
  }

  function start(trigger: TemplateV2AutosaveTrigger): Promise<void> {
    if (inFlight) return inFlight;
    if (!hasPendingSnapshot) return Promise.resolve();

    const snapshot = pendingSnapshot;
    inFlightSnapshot = snapshot;
    const context: TemplateV2AutosaveContext = { trigger };
    hasPendingSnapshot = false;
    ready = false;

    let task: Promise<void>;
    task = Promise.resolve().then(async () => {
      let succeeded = false;
      try {
        const result = await options.save(snapshot, context);
        await options.onSaved?.(snapshot, result, context);
        blockedByError = false;
        succeeded = true;
      } catch (error) {
        // Preserve a newer edit when one arrived during the failed request.
        // Otherwise retain the failed snapshot for an explicit retry.
        if (!hasPendingSnapshot) {
          pendingSnapshot = snapshot;
          hasPendingSnapshot = true;
        }
        clearDebounce();
        ready = false;
        blockedByError = true;
        reportError(error, snapshot, context);
        throw error;
      } finally {
        if (inFlight === task) {
          inFlight = null;
          inFlightSnapshot = undefined;
        }
      }

      if (
        succeeded &&
        hasPendingSnapshot &&
        ready &&
        (!closing || disposeShouldFlush)
      ) {
        await start(closing ? "dispose" : "queued");
      }
    });
    inFlight = task;
    return task;
  }

  function armDebounce(): void {
    clearDebounce();
    if (
      !accepting ||
      !hasPendingSnapshot ||
      blockedByError ||
      disposed
    ) {
      return;
    }
    timerArmed = true;
    timerHandle = timers.setTimeout(() => {
      timerArmed = false;
      timerHandle = undefined;
      ready = true;
      void start("debounce").catch(() => {
        // onError receives the failure. Automatic retries are intentionally
        // blocked until flush() or resume() is called by the owner.
      });
    }, options.debounceMs);
  }

  async function drain(trigger: "flush" | "dispose"): Promise<void> {
    while (true) {
      clearDebounce();
      if (hasPendingSnapshot) ready = true;
      if (inFlight) {
        await inFlight;
        continue;
      }
      if (!hasPendingSnapshot) return;
      await start(trigger);
    }
  }

  return {
    schedule(snapshot) {
      if (!accepting || disposed) return false;
      if (
        inFlight &&
        !hasPendingSnapshot &&
        Object.is(snapshot, inFlightSnapshot)
      ) {
        return true;
      }
      pendingSnapshot = snapshot;
      hasPendingSnapshot = true;
      ready = false;
      armDebounce();
      return true;
    },

    async flush() {
      if (disposed) return;
      await drain("flush");
    },

    resume() {
      if (!accepting || disposed) return;
      blockedByError = false;
      armDebounce();
    },

    discardPending() {
      clearDebounce();
      hasPendingSnapshot = false;
      ready = false;
      blockedByError = false;
    },

    dispose(disposeOptions = {}) {
      if (disposePromise) return disposePromise;
      accepting = false;
      closing = true;
      disposeShouldFlush = disposeOptions.flush ?? true;
      clearDebounce();
      disposePromise = (async () => {
        try {
          if (disposeShouldFlush) {
            await drain("dispose");
          } else if (inFlight) {
            // Do not cancel a request that already crossed the persistence
            // boundary, even when the pending debounce is intentionally
            // abandoned.
            await inFlight;
          }
        } finally {
          hasPendingSnapshot = false;
          ready = false;
          disposed = true;
          closing = false;
        }
      })();
      return disposePromise;
    },

    getState() {
      return {
        pending: hasPendingSnapshot,
        inFlight: inFlight !== null,
        blockedByError,
        closing,
        disposed,
      };
    },
  };
}
