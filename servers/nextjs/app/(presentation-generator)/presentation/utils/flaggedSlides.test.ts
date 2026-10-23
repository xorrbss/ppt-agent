import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveFlaggedSlideMap,
  describeFlaggedSlideReason,
  flaggedSlideCategories,
  flaggedSlideCount,
  flaggedSlideKey,
  flaggedSlidePositions,
  isReviewRequiredDeck,
  flaggedSlideStage,
  readFlaggedSlideEntries,
} from "./flaggedSlides.ts";

const gateEntry = {
  index: 2,
  slideId: "s-c",
  reason: "composition_safety:overflow,clipping",
  remakeAttempted: true,
  remakeOutcome: "remake_budget_exhausted",
  remakeCount: 2,
};
const authorEntry = {
  index: 0,
  slideId: "s-a",
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
      slideId: "s-c",
      reason: "composition_safety:overflow,clipping",
      reasons: undefined,
      remakeAttempted: true,
      remakeOutcome: "remake_budget_exhausted",
      remakeCount: 2,
      repeated: undefined,
      classification: undefined,
    },
  ]);
});

test("readFlaggedSlideEntries tolerates a missing or foreign theme", () => {
  assert.deepEqual(readFlaggedSlideEntries(null), []);
  assert.deepEqual(readFlaggedSlideEntries({ mode: "authored" }), []);
  assert.deepEqual(readFlaggedSlideEntries({ authoredQualityGate: { status: "passed" } }), []);
  assert.deepEqual(readFlaggedSlideEntries("theme"), []);
});

test("readFlaggedSlideEntries reads the merged 'reasons' list when present", () => {
  const entries = readFlaggedSlideEntries({
    authoredQualityGate: {
      flaggedSlides: [
        {
          index: 0,
          slideId: "s-a",
          reason: "author:attempts_exhausted",
          reasons: ["author:attempts_exhausted", "composition_safety:overlap"],
        },
      ],
    },
  });
  assert.deepEqual(entries[0].reasons, [
    "author:attempts_exhausted",
    "composition_safety:overlap",
  ]);
});

test("deriveFlaggedSlideMap keys badges by slide id so a reorder keeps them", () => {
  const map = deriveFlaggedSlideMap(deck([gateEntry, authorEntry]));
  assert.deepEqual(Object.keys(map).sort(), ["s-a", "s-c"]);
  assert.deepEqual(map["s-c"], {
    slideId: "s-c",
    slideIndex: 2,
    reason: "composition_safety:overflow,clipping",
    reasons: ["composition_safety:overflow,clipping"],
    stage: "gate",
    categories: ["overflow", "clipping"],
    remakeAttempted: true,
    remakeOutcome: "remake_budget_exhausted",
    classification: null,
  });
  assert.equal(map["s-a"].stage, "author");
  assert.deepEqual(map["s-a"].categories, []);
  assert.equal(flaggedSlideCount(map), 2);
});

test("deriveFlaggedSlideMap matches by slideId even when the receipt's index no longer names that slide", () => {
  // Slides served out of order (or after a reorder renumbered every index)
  // still resolve to the slide the receipt's id names, not whichever slide
  // now happens to carry the recorded index.
  const slides = [
    { id: "s-c", index: 2 },
    { id: "s-a", index: 0 },
    { id: "s-b", index: 1 },
  ];
  const map = deriveFlaggedSlideMap(deck([gateEntry], slides));
  assert.deepEqual(Object.keys(map), ["s-c"]);
});

test("a flag survives a full derive, reorder, persist, re-derive cycle (defect 1 regression)", () => {
  // Generation flags slide "s-a" (index 0). This is the state the editor
  // loads first.
  const original = deck(
    [{ index: 0, slideId: "s-a", reason: "author:attempts_exhausted" }],
    [
      { id: "s-a", index: 0 },
      { id: "s-b", index: 1 },
      { id: "s-c", index: 2 },
    ],
  );
  const firstMap = deriveFlaggedSlideMap(original);
  assert.deepEqual(Object.keys(firstMap), ["s-a"]);
  assert.equal(firstMap["s-a"].slideId, "s-a");

  // The user drags "s-a" to the end. SidePanel.tsx's handleDragEnd reorders
  // the slides array and then renumbers every slide's `index` field
  // sequentially (0..n-1) before useAutoSave persists it -- exactly what
  // production does, regardless of what this map looked like.
  const reorderedSlides = [
    { id: "s-b", index: 0 },
    { id: "s-c", index: 1 },
    { id: "s-a", index: 2 },
  ];

  // The deck is reloaded (or the page remounts): the theme travels with it
  // unchanged, but the slides array now carries the persisted new indices.
  const reloaded = { theme: original.theme, slides: reorderedSlides };
  const secondMap = deriveFlaggedSlideMap(reloaded);

  // The flag must still name "s-a" -- not "s-b", which now holds index 0.
  assert.deepEqual(Object.keys(secondMap), ["s-a"]);
  assert.equal(secondMap["s-a"].slideId, "s-a");
  assert.equal(secondMap["s-b"], undefined);
});

test("deriveFlaggedSlideMap falls back to the index field for a legacy receipt with no slideId", () => {
  // A receipt persisted before slideId existed. This is the documented
  // remaining failure mode: it resolves correctly only until the deck is
  // reordered, exactly like before this fix.
  const legacyEntry = { index: 2, reason: "composition_safety:overflow" };
  const slides = [
    { id: "s-c", index: 2 },
    { id: "s-a", index: 0 },
    { id: "s-b", index: 1 },
  ];
  const map = deriveFlaggedSlideMap(deck([legacyEntry], slides));
  assert.deepEqual(Object.keys(map), ["s-c"]);
});

test("deriveFlaggedSlideMap falls back to a positional key for id-less slides", () => {
  const slides = [{ index: 0 }, { index: 1 }, { index: 2 }];
  const legacyEntry = { index: 2, reason: "composition_safety:overflow" };
  const map = deriveFlaggedSlideMap(deck([legacyEntry], slides));
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
  assert.deepEqual(flaggedSlideCategories("composition_safety:overflow, clipping ,"), [
    "overflow",
    "clipping",
  ]);
  assert.deepEqual(flaggedSlideCategories("composition_safety:unknown"), []);
  assert.deepEqual(flaggedSlideCategories("author:attempts_exhausted"), []);
});

test("describeFlaggedSlideReason localizes each category token instead of showing the raw one", () => {
  const calls: Array<[string, Record<string, string | number> | undefined]> = [];
  const t = (key: string, values?: Record<string, string | number>) => {
    calls.push([key, values]);
    return key === "category.overflow"
      ? "content overflow"
      : key === "category.clipping"
        ? "clipped content"
        : key;
  };
  const map = deriveFlaggedSlideMap(deck([gateEntry, authorEntry]));

  assert.equal(describeFlaggedSlideReason(t, map["s-a"]), "reason.author");

  const gateText = describeFlaggedSlideReason(t, map["s-c"]);
  assert.equal(gateText, "reason.gate");
  // Both raw tokens went through the category translator...
  assert.ok(calls.some(([key]) => key === "category.overflow"));
  assert.ok(calls.some(([key]) => key === "category.clipping"));
  // ...and the sentence was built from the localized labels, not the raw
  // "overflow"/"clipping" tokens.
  const gateCall = calls.find(([key]) => key === "reason.gate");
  assert.deepEqual(gateCall, [
    "reason.gate",
    { categories: "content overflow, clipped content" },
  ]);

  const unknownGate = deriveFlaggedSlideMap(
    deck([{ index: 1, slideId: "s-b", reason: "composition_safety:unknown" }]),
  )["s-b"];
  assert.equal(describeFlaggedSlideReason(t, unknownGate), "reason.gateUnknown");

  const other = deriveFlaggedSlideMap(
    deck([{ index: 1, slideId: "s-b", reason: "custom_reason" }]),
  )["s-b"];
  assert.equal(describeFlaggedSlideReason(t, other), "reason.other");
  assert.deepEqual(calls.at(-1), ["reason.other", { reason: "custom_reason" }]);
});

test("describeFlaggedSlideReason falls back to the raw token for an unmapped category", () => {
  const t = (key: string) => key;
  const badge = deriveFlaggedSlideMap(
    deck([{ index: 0, slideId: "s-a", reason: "composition_safety:some_future_category" }]),
  )["s-a"];
  assert.equal(describeFlaggedSlideReason(t, badge), "reason.gate");
});

test("describeFlaggedSlideReason describes every reason when a slide was flagged by both stages (defect 3)", () => {
  const t = (key: string, values?: Record<string, string | number>) => {
    if (key === "category.overlap") return "overlapping elements";
    if (key === "reason.gate") return `gate:${values?.categories}`;
    return key;
  };
  const merged = deriveFlaggedSlideMap(
    deck([
      {
        index: 0,
        slideId: "s-a",
        reason: "author:attempts_exhausted",
        reasons: ["author:attempts_exhausted", "composition_safety:overlap"],
      },
    ]),
  )["s-a"];

  assert.deepEqual(merged.reasons, [
    "author:attempts_exhausted",
    "composition_safety:overlap",
  ]);
  const text = describeFlaggedSlideReason(t, merged);
  // Both the author-stage message and the gate-stage message are present --
  // neither reason silently disappears.
  assert.ok(text.includes("reason.author"));
  assert.ok(text.includes("gate:overlapping elements"));
});

test("every category the backend gate can emit has a localized label", () => {
  // Mirrors ``utils/authored_dom_categories.BLOCKING_SAFETY_CATEGORIES`` plus
  // the two whole-reason tokens ``authored_terminal_slide_policy`` emits. Bench
  // run 36 (2026-09-05) is why this list is asserted rather than assumed: two
  // detectors were registered backend-side and every consumer that mapped a
  // category -- this one included -- was left behind.
  const emitted = [
    "overflow",
    "clipping",
    "off_canvas",
    "overlap",
    "text_escapes_card",
    "text_intrudes_footer_zone",
    "repair_budget_exhausted",
    "gate_repair_not_admitted",
  ];
  const seen: string[] = [];
  const t = (key: string) => {
    seen.push(key);
    return key;
  };

  for (const category of emitted) {
    const badge = deriveFlaggedSlideMap(
      deck([{ index: 1, slideId: "s-b", reason: `composition_safety:${category}` }]),
    )["s-b"];
    describeFlaggedSlideReason(t, badge);
  }

  // A category with no label would come back as the raw token instead of a
  // ``category.*`` key.
  assert.equal(
    seen.filter((key) => key.startsWith("category.")).length,
    emitted.length,
  );
  assert.ok(seen.includes("category.textEscapesCard"));
  assert.ok(seen.includes("category.textIntrudesFooterZone"));
});

function svgDeck(flaggedSlides: unknown, slides: unknown[] = defaultSlides()) {
  return { theme: { svg: { qualityStatus: "review_required", flaggedSlides } }, slides };
}

test("SVG decks badge the pages listed on theme.svg.flaggedSlides", () => {
  const presentation = svgDeck([
    { index: 1, slideId: "s-b", reason: "design:information_structure", classification: "review_required" },
    { index: 0, slideId: "s-a", reason: "svg:text_overlap", classification: "repair_not_admitted" },
    { index: 2, slideId: "s-c", reason: "author:placeholder", classification: "review_required" },
  ]);
  const map = deriveFlaggedSlideMap(presentation);
  assert.deepEqual(Object.keys(map).sort(), ["s-b", "s-c"]);
  assert.deepEqual(flaggedSlidePositions(presentation), [1, 2]);
});

test("describeFlaggedSlideReason maps the page-quality reasons to specific labels", () => {
  const calls: Array<[string, Record<string, string | number> | undefined]> = [];
  const t = (key: string, values?: Record<string, string | number>) => {
    calls.push([key, values]);
    return key;
  };
  const describe = (reason: string) => {
    const badge = deriveFlaggedSlideMap(svgDeck([{ index: 0, slideId: "s-a", reason }]))["s-a"];
    return describeFlaggedSlideReason(t, badge);
  };
  assert.equal(describe("design:unreviewed"), "reason.designUnreviewed");
  assert.equal(describe("author:placeholder"), "reason.placeholder");
  assert.equal(describe("author:source_verbatim_fallback"), "reason.sourceVerbatimFallback");
  assert.equal(describe("source_visibility"), "reason.sourceVisibility");
  assert.equal(describe("svg:text_overlap"), "reason.svg");
  assert.deepEqual(calls.at(-1), ["reason.svg", { code: "text_overlap" }]);
  assert.equal(describe("design:information_structure,density_balance"), "reason.design");
  assert.deepEqual(calls.at(-1), [
    "reason.design",
    { axes: "designAxis.informationStructure, designAxis.densityBalance" },
  ]);
  // A generic author reason keeps the generic placeholder text.
  assert.equal(describe("author:attempts_exhausted"), "reason.author");
});

test("isReviewRequiredDeck reads only theme.svg.qualityStatus", () => {
  assert.equal(isReviewRequiredDeck({ svg: { qualityStatus: "review_required" } }), true);
  assert.equal(isReviewRequiredDeck({ svg: { qualityStatus: "passed" } }), false);
  assert.equal(isReviewRequiredDeck({ authoredQualityGate: { flaggedSlides: [] } }), false);
  assert.equal(isReviewRequiredDeck(null), false);
});
