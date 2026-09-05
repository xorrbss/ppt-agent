import assert from "node:assert/strict";
import test from "node:test";

import {
  FALLBACK_TIME_ZONE,
  formatViewerDate,
  formatViewerTimestamp,
  parseBackendTimestamp,
  resolveViewerTimeZone,
} from "./viewer-time.ts";

function withTimeZone<T>(zone: string, run: () => T): T {
  const previous = process.env.TZ;
  process.env.TZ = zone;
  try {
    return run();
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
}

test("viewer time zone follows the runtime zone, not a fixed UTC", () => {
  withTimeZone("Asia/Seoul", () => {
    assert.equal(resolveViewerTimeZone(), "Asia/Seoul");
  });
  withTimeZone("America/New_York", () => {
    assert.equal(resolveViewerTimeZone(), "America/New_York");
  });
});

test("viewer time zone falls back to UTC when Intl cannot resolve one", () => {
  const original = Intl.DateTimeFormat;
  try {
    (Intl as { DateTimeFormat: unknown }).DateTimeFormat = function () {
      throw new Error("unavailable");
    };
    assert.equal(resolveViewerTimeZone(), FALLBACK_TIME_ZONE);
  } finally {
    Intl.DateTimeFormat = original;
  }
});

test("formatViewerTimestamp renders UTC instants in the viewer's time zone", () => {
  withTimeZone("Asia/Seoul", () => {
    const label = formatViewerTimestamp("2026-08-03T01:00:00Z", "ko-KR") ?? "";
    assert.match(label, /10:00/);
    assert.doesNotMatch(label, /UTC|GMT/);
  });
  withTimeZone("America/New_York", () => {
    assert.match(formatViewerTimestamp("2026-08-03T01:00:00Z", "en-US") ?? "", /9:00 PM/);
  });
});

test("offset-less backend values are read as UTC, not local wall-clock time", () => {
  withTimeZone("Asia/Seoul", () => {
    assert.equal(
      parseBackendTimestamp("2026-08-03T01:00:00")?.getTime(),
      Date.UTC(2026, 7, 3, 1, 0, 0),
    );
    assert.equal(
      formatViewerTimestamp("2026-08-03T01:00:00", "ko-KR"),
      formatViewerTimestamp("2026-08-03T01:00:00Z", "ko-KR"),
    );
  });
});

test("empty and malformed values are rejected", () => {
  assert.equal(formatViewerTimestamp(undefined), undefined);
  assert.equal(formatViewerTimestamp(""), undefined);
  assert.equal(formatViewerTimestamp("   "), undefined);
  assert.equal(formatViewerTimestamp("not-a-date"), undefined);
});

test("formatViewerDate rolls the calendar day over in the viewer's zone", () => {
  // 22:00 UTC is already the next calendar day in Seoul (07:00) but not in
  // New York (18:00 the same day).
  withTimeZone("Asia/Seoul", () => {
    assert.match(formatViewerDate("2026-08-03T22:00:00Z", "en-US") ?? "", /8\/4\/2026/);
  });
  withTimeZone("America/New_York", () => {
    assert.match(formatViewerDate("2026-08-03T22:00:00Z", "en-US") ?? "", /8\/3\/2026/);
  });
});

test("formatViewerDate rejects empty and malformed values", () => {
  assert.equal(formatViewerDate(undefined), undefined);
  assert.equal(formatViewerDate("not-a-date"), undefined);
});
