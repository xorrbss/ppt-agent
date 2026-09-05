import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveFlaggedSlideMap,
  describeFlaggedSlideReason,
  flaggedSlideCategories,
  flaggedSlideCount,
  flaggedSlideKey,
  flaggedSlideStage,
  readFlaggedSlideEntries,
} from "./flaggedSlides.ts";

const gateEntry = {
  index: 2,
  reason: "composition_safety:overflow,text_collision",
  remakeAttempted: true,
  remakeOutcome: "remake_budget_exhausted",
  remakeCount: 2,
};
const authorEntry = {
  index: 0,
  reason: "author:attempts_exhausted",
  remakeAttempted: true,
  remakeOutcome: "placeholder",
};

function deck(flaggedSlides: unknown, slides: unknown[] = defaultSlides()) {
  return {
    theme: { mode: "authored", authoredQualityGate: { flaggedSlides } },
    slides,
  };
}

function defaultSlides() {
  return [
    { id: "s-a", index: 0 },
    { id: "s-b", index: 1 },
    { id: "s-c", index: 2 },
  ];
}

test("readFlaggedSlideEntries keeps only well-formed receipt rows", () => {
  const entries = readFlaggedSlideEntries({
    authoredQualityGate: {
      flaggedSlides: [
        gateEntry,
        { index: "1", reason: "composition_safety:overflow" },
        { index: 1 },
        { index: -1, reason: "x" },
        "junk",
      ],
    },
  });
  assert.deepEqual(entries, [
    {
      index: 2,
      reason: "composition_safety:overflow,text_collision",
      remakeAttempted: true,
      remakeOutcome: "remake_budget_exhausted",
      remakeCount: 2,
      repeated: undefined,
    },
  ]);
});

test("readFlaggedSlideEntries tolerates a missing or foreign theme", () => {
  assert.deepEqual(readFlaggedSlideEntries(null), []);
  assert.deepEqual(readFlaggedSlideEntries({ mode: "authored" }), []);
  assert.deepEqual(readFlaggedSlideEntries({ authoredQualityGate: { status: "passed" } }), []);
  assert.deepEqual(readFlaggedSlideEntries("theme"), []);
});

test("deriveFlaggedSlideMap keys badges by slide id so a reorder keeps them", () => {
  const map = deriveFlaggedSlideMap(deck([gateEntry, authorEntry]));
  assert.deepEqual(Object.keys(map).sort(), ["s-a", "s-c"]);
  assert.deepEqual(map["s-c"], {
    slideId: "s-c",
    slideIndex: 2,
    reason: "composition_safety:overflow,text_collision",
    stage: "gate",
    categories: ["overflow", "text_collision"],
    remakeAttempted: true,
    remakeOutcome: "remake_budget_exhausted",
  });
  assert.equal(map["s-a"].stage, "author");
  assert.deepEqual(map["s-a"].categories, []);
  assert.equal(flaggedSlideCount(map), 2);
});

test("deriveFlaggedSlideMap matches on the slide index field before list position", () => {
  // Slides served out of order still resolve to the slide whose index the
  // receipt recorded.
  const slides = [
    { id: "s-c", index: 2 },
    { id: "s-a", index: 0 },
    { id: "s-b", index: 1 },
  ];
  const map = deriveFlaggedSlideMap(deck([gateEntry], slides));
  assert.deepEqual(Object.keys(map), ["s-c"]);
});

test("deriveFlaggedSlideMap falls back to a positional key for id-less slides", () => {
  const slides = [{ index: 0 }, { index: 1 }, { index: 2 }];
  const map = deriveFlaggedSlideMap(deck([gateEntry], slides));
  assert.deepEqual(Object.keys(map), ["index:2"]);
  assert.equal(map["index:2"].slideId, null);
  assert.equal(flaggedSlideKey(slides[2], 2), "index:2");
  assert.equal(flaggedSlideKey({ id: "s-z" }, 9), "s-z");
});

test("deriveFlaggedSlideMap is empty without a receipt", () => {
  assert.deepEqual(deriveFlaggedSlideMap(null), {});
  assert.deepEqual(deriveFlaggedSlideMap({ theme: null, slides: defaultSlides() }), {});
  assert.deepEqual(deriveFlaggedSlideMap(deck([])), {});
  assert.equal(flaggedSlideCount(undefined), 0);
});

test("stage and categories follow the reason prefix", () => {
  assert.equal(flaggedSlideStage("author:attempts_exhausted"), "author");
  assert.equal(flaggedSlideStage("composition_safety:overflow"), "gate");
  assert.equal(flaggedSlideStage("something_else"), "gate");
  assert.deepEqual(flaggedSlideCategories("composition_safety:overflow, clipped ,"), [
    "overflow",
    "clipped",
  ]);
  assert.deepEqual(flaggedSlideCategories("composition_safety:unknown"), []);
  assert.deepEqual(flaggedSlideCategories("author:attempts_exhausted"), []);
});

test("describeFlaggedSlideReason picks the message per stage", () => {
  const calls: Array<[string, Record<string, string | number> | undefined]> = [];
  const t = (key: string, values?: Record<string, string | number>) => {
    calls.push([key, values]);
    return key;
  };
  const map = deriveFlaggedSlideMap(deck([gateEntry, authorEntry]));
  assert.equal(describeFlaggedSlideReason(t, map["s-a"]), "reason.author");
  assert.equal(describeFlaggedSlideReason(t, map["s-c"]), "reason.gate");
  assert.deepEqual(calls[1], ["reason.gate", { categories: "overflow, text_collision" }]);

  const unknownGate = deriveFlaggedSlideMap(
    deck([{ index: 1, reason: "composition_safety:unknown" }]),
  )["s-b"];
  assert.equal(describeFlaggedSlideReason(t, unknownGate), "reason.gateUnknown");

  const other = deriveFlaggedSlideMap(deck([{ index: 1, reason: "custom_reason" }]))["s-b"];
  assert.equal(describeFlaggedSlideReason(t, other), "reason.other");
  assert.deepEqual(calls.at(-1), ["reason.other", { reason: "custom_reason" }]);
});
