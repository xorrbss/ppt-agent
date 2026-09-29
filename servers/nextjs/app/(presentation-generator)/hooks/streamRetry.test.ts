import assert from "node:assert/strict";
import test, { mock } from "node:test";

import {
  MAX_STREAM_RETRIES,
  STREAM_RETRY_DELAY_MS,
  createStreamRetryScheduler,
} from "./streamRetry";

function silenceWarn() {
  const warn = mock.method(console, "warn", () => undefined);
  return () => warn.mock.restore();
}

test("retries at most MAX_STREAM_RETRIES times with linear backoff", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  const restore = silenceWarn();
  const events: string[] = [];
  const retry = createStreamRetryScheduler({
    streamName: "Test",
    beforeRetry: () => events.push("before"),
    reopen: () => events.push("reopen"),
  });
  try {
    for (let attempt = 1; attempt <= MAX_STREAM_RETRIES; attempt += 1) {
      assert.equal(retry.schedule(`attempt ${attempt}`), true);
      mock.timers.tick(STREAM_RETRY_DELAY_MS * attempt - 1);
      assert.equal(events.at(-1), "before");
      mock.timers.tick(1);
      assert.equal(events.at(-1), "reopen");
    }
    assert.equal(retry.schedule("one too many"), false);
    assert.equal(events.filter((event) => event === "reopen").length, MAX_STREAM_RETRIES);

    retry.reset();
    assert.equal(retry.schedule("after reset"), true);
  } finally {
    restore();
    mock.timers.reset();
  }
});

test("close cancels the pending reconnect and refuses further retries", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  const restore = silenceWarn();
  let reopened = 0;
  const retry = createStreamRetryScheduler({
    streamName: "Test",
    beforeRetry: () => undefined,
    reopen: () => {
      reopened += 1;
    },
  });
  try {
    assert.equal(retry.closed, false);
    assert.equal(retry.schedule("lost"), true);
    retry.close();
    mock.timers.tick(STREAM_RETRY_DELAY_MS * MAX_STREAM_RETRIES);
    assert.equal(reopened, 0);
    assert.equal(retry.closed, true);
    assert.equal(retry.schedule("after close"), false);
  } finally {
    restore();
    mock.timers.reset();
  }
});
