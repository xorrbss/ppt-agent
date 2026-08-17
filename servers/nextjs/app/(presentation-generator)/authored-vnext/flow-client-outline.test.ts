import assert from "node:assert/strict";
import test from "node:test";

import { confirmedDirectionEvents } from "./flow-client-outline";

test("confirmed direction produces the pinned direction and four-layout outline", () => {
  const events = confirmedDirectionEvents(
    {
      tone: ["evidence"],
      imagery: "chart",
      composition: "grid",
      notes: "keep it sparse",
    },
    "Board update",
    { coverPurpose: "Cover", slidePurpose: (ordinal) => `Slide ${ordinal}` },
    90,
  );

  assert.equal(events[0].type, "visualDirection.confirmed");
  assert.equal(events[1].type, "outline.succeeded");
  if (events[1].type !== "outline.succeeded")
    throw new Error("missing outline");
  assert.equal(events[1].plan.slides.length, 90);
  assert.deepEqual(
    [...new Set(events[1].plan.slides.map((slide) => slide.layoutId))].sort(),
    ["layout:cover", "layout:long-title", "layout:section", "layout:standard"],
  );
});

test("development outline accepts the explicit three-slide harness count", () => {
  const events = confirmedDirectionEvents(
    { tone: ["evidence"], imagery: "chart", composition: "grid" },
    "Board update",
    { coverPurpose: "Cover", slidePurpose: (ordinal) => `Slide ${ordinal}` },
    3,
  );
  assert.equal(events[1].type, "outline.succeeded");
  if (events[1].type !== "outline.succeeded") throw new Error("missing outline");
  assert.equal(events[1].plan.slides.length, 3);
});
