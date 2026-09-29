/**
 * Retry policy shared by the outline and presentation SSE streams: at most
 * MAX_STREAM_RETRIES reconnects, linearly backed off (1s, 2s, 3s), never
 * after the stream has been closed. The scheduler owns the retry counter,
 * the pending timer and the closed flag so both hooks enforce the same
 * policy at one point.
 */
export const MAX_STREAM_RETRIES = 3;
export const STREAM_RETRY_DELAY_MS = 1_000;

export interface StreamRetryScheduler {
  /** True once close() ran; the stream must not reopen after this. */
  readonly closed: boolean;
  /** Schedules a reconnect; false when the budget is spent or the stream is closed. */
  schedule(reason: string): boolean;
  /** Forgets the retries used so far (a healthy stream earns a fresh budget). */
  reset(): void;
  /** Cancels a pending reconnect without closing the stream. */
  clearTimer(): void;
  /** Marks the stream closed and cancels any pending reconnect. */
  close(): void;
}

export interface StreamRetryOptions {
  /** Console-warning prefix, e.g. "Outline" or "Presentation" (not user-facing). */
  streamName: string;
  /** Runs before the timer is armed: close the source and reset partial state. */
  beforeRetry: () => void;
  /** Reopens the stream once the delay elapses (skipped when closed meanwhile). */
  reopen: () => void;
}

export function createStreamRetryScheduler(
  options: StreamRetryOptions,
): StreamRetryScheduler {
  let retryCount = 0;
  let closed = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const clearTimer = () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  };

  return {
    get closed() {
      return closed;
    },
    schedule(reason) {
      if (retryCount >= MAX_STREAM_RETRIES || closed) {
        return false;
      }
      retryCount += 1;
      const retryDelay = STREAM_RETRY_DELAY_MS * retryCount;
      console.warn(
        `${options.streamName} stream retry ${retryCount}/${MAX_STREAM_RETRIES}: ${reason}`,
      );
      options.beforeRetry();
      clearTimer();
      timer = setTimeout(() => {
        if (!closed) {
          options.reopen();
        }
      }, retryDelay);
      return true;
    },
    reset() {
      retryCount = 0;
    },
    clearTimer,
    close() {
      closed = true;
      clearTimer();
    },
  };
}
