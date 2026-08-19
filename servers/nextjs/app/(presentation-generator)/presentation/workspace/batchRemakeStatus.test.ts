import assert from "node:assert/strict";
import test from "node:test";

import {
  batchRemakeIsComplete,
  BATCH_REMAKE_PREVIEW_KIND,
  BATCH_REMAKE_STALL_MS,
  batchRemakeIsStalled,
  decodeBatchRemakePayload,
  readyPreviews,
} from "./batchRemakeStatus.ts";

const previewing = {
  kind: "authored_batch_remake_preview",
  phase: "previewing",
  presentation_id: "11111111-1111-1111-1111-111111111111",
  total: 3,
  completed: 1,
  slides: [
    {
      slide_index: 9,
      status: "failed",
      preview_task_id: "child-9",
      reason_code: "authored_page_remake_source_render_failed",
      stage: "source_render",
      retryable: true,
    },
    { slide_index: 4, status: "pending", preview_task_id: "child-4" },
    {
      slide_index: 7,
      status: "previewed",
      preview_task_id: "child-7",
      candidate_image: "authored/pid/slide_7_abc.png",
      candidate_digest: "abc",
      source_digest: "def",
      warning: true,
    },
  ],
};

test("a foreign envelope is never mistaken for batch progress", () => {
  assert.equal(decodeBatchRemakePayload(null), null);
  assert.equal(
    decodeBatchRemakePayload({ kind: "authored_page_remake_preview" }),
    null,
  );
});

test("slides are ordered by page and keep their per-slide outcome", () => {
  const payload = decodeBatchRemakePayload(previewing);
  assert.ok(payload);
  assert.deepEqual(
    payload.slides.map((slide) => slide.slideIndex),
    [4, 7, 9],
  );
  assert.deepEqual(
    payload.slides.map((slide) => slide.status),
    ["pending", "previewed", "failed"],
  );
  assert.equal(payload.slides[1].warning, true);
  assert.equal(payload.slides[1].candidateImage, "authored/pid/slide_7_abc.png");
  assert.equal(
    payload.slides[2].reasonCode,
    "authored_page_remake_source_render_failed",
  );
  assert.equal(payload.total, 3);
  assert.equal(payload.completed, 1);
  assert.equal(batchRemakeIsComplete(payload), false);
});

test("only rendered candidates are offered for approval", () => {
  const payload = decodeBatchRemakePayload(previewing);
  assert.deepEqual(readyPreviews(payload), [
    { slide_index: 7, preview_task_id: "child-7" },
  ]);
  assert.deepEqual(readyPreviews(null), []);
});

test("a previewed slide with no candidate image is not approvable", () => {
  // The user must see what they approve; an image-less "previewed" entry is a
  // backend contract violation and is dropped rather than silently published.
  const payload = decodeBatchRemakePayload({
    ...previewing,
    slides: [
      { slide_index: 2, status: "previewed", preview_task_id: "child-2" },
    ],
  });
  assert.ok(payload);
  assert.deepEqual(payload.slides, []);
  assert.deepEqual(readyPreviews(payload), []);
});

test("an unknown per-slide status degrades to pending, never to approvable", () => {
  const payload = decodeBatchRemakePayload({
    ...previewing,
    slides: [
      { slide_index: 3, status: "published", preview_task_id: "child-3" },
    ],
  });
  assert.ok(payload);
  assert.equal(payload.slides[0].status, "pending");
  assert.deepEqual(readyPreviews(payload), []);
});

test("completion follows the phase, and the counters as a fallback", () => {
  assert.equal(
    batchRemakeIsComplete(
      decodeBatchRemakePayload({ ...previewing, phase: "preview_ready" }),
    ),
    true,
  );
  assert.equal(
    batchRemakeIsComplete(
      decodeBatchRemakePayload({ ...previewing, completed: 3 }),
    ),
    true,
  );
});

test("a batch that stops advancing is reported as stalled, not merely slow", () => {
  // A cancelled fan-out freezes `completed` instead of erroring, and nothing
  // sweeps the parent task, so waiting it out costs the full 15-minute deadline.
  const payload = decodeBatchRemakePayload({
    kind: BATCH_REMAKE_PREVIEW_KIND,
    phase: "previewing",
    total: 3,
    completed: 1,
    slides: [],
  });
  const lastProgressAt = 1_000_000;

  assert.equal(
    batchRemakeIsStalled(payload, {
      lastProgressAt,
      now: lastProgressAt + BATCH_REMAKE_STALL_MS - 1,
    }),
    false,
  );
  assert.equal(
    batchRemakeIsStalled(payload, {
      lastProgressAt,
      now: lastProgressAt + BATCH_REMAKE_STALL_MS,
    }),
    true,
  );
});

test("a finished batch is never stalled however long it sat there", () => {
  const payload = decodeBatchRemakePayload({
    kind: BATCH_REMAKE_PREVIEW_KIND,
    phase: "preview_ready",
    total: 2,
    completed: 2,
    slides: [],
  });

  assert.equal(
    batchRemakeIsStalled(payload, { lastProgressAt: 0, now: 10 ** 9 }),
    false,
  );
});
