import assert from "node:assert/strict";
import test from "node:test";

import {
  buildPowerPointCalibrationProbes,
  derivePowerPointCalibrationProfiles,
} from "./powerpoint-desktop-calibration.ts";

test("PowerPoint calibration matrix covers every shaping dimension", () => {
  const probes = buildPowerPointCalibrationProbes();

  assert.equal(probes.length, 128);
  assert.equal(new Set(probes.map((probe) => probe.id)).size, 128);
  assert.deepEqual(
    new Set(probes.map((probe) => probe.fontWeight)),
    new Set([400, 700])
  );
  assert.ok(probes.some((probe) => probe.lineMode === "multiline"));
  assert.ok(probes.some((probe) => probe.horizontalAlignment === "center"));
  assert.ok(probes.some((probe) => probe.widthMode === "content"));
});

test("calibration profiles retain font, line, alignment, and width buckets", () => {
  const measurement = (overrides = {}) => ({
    id: "regular-24-single-left-fixed",
    fontFamily: "Noto Sans KR",
    fontWeight: 400,
    fontSizePt: 24,
    lineMode: "single",
    horizontalAlignment: "left",
    widthMode: "fixed",
    text: "가나다",
    boxBoundsPt: { left: 10, top: 20, width: 200, height: 60 },
    textBoundsPt: { left: 10, top: 23, width: 120, height: 30 },
    lineCount: 1,
    ...overrides,
  });
  const profiles = derivePowerPointCalibrationProfiles([
    measurement(),
    measurement({ textBoundsPt: { left: 10, top: 25, width: 140, height: 34 } }),
    measurement({ horizontalAlignment: "center", id: "regular-24-single-center-fixed" }),
  ]);

  assert.equal(profiles.length, 2);
  assert.deepEqual(profiles[0], {
    fontFamily: "Noto Sans KR",
    fontWeight: 400,
    fontSizePt: 24,
    lineMode: "single",
    horizontalAlignment: "left",
    widthMode: "fixed",
    sampleCount: 2,
    widthScale: 0.65,
    baselineOffsetPt: 4,
    lineBoxHeightPt: 32,
  });
});
