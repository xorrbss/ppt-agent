export const POWERPOINT_CALIBRATION_FONT_FAMILY = "Noto Sans KR";

export const POWERPOINT_CALIBRATION_FONT_WEIGHTS = [
  { label: "regular", value: 400 },
  { label: "bold", value: 700 },
] as const;

export const POWERPOINT_CALIBRATION_FONT_SIZES_PT = [
  9, 12, 16, 20, 24, 32, 40, 54,
] as const;

export const POWERPOINT_CALIBRATION_LINE_MODES = [
  "single",
  "multiline",
] as const;

export const POWERPOINT_CALIBRATION_ALIGNMENTS = ["left", "center"] as const;
export const POWERPOINT_CALIBRATION_WIDTH_MODES = ["fixed", "content"] as const;

export interface PowerPointCalibrationProbe {
  readonly id: string;
  readonly fontFamily: typeof POWERPOINT_CALIBRATION_FONT_FAMILY;
  readonly fontWeight: 400 | 700;
  readonly fontSizePt: number;
  readonly lineMode: "single" | "multiline";
  readonly horizontalAlignment: "left" | "center";
  readonly widthMode: "fixed" | "content";
  readonly text: string;
}

export interface PowerPointTextBoundsPt {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

export interface PowerPointCalibrationMeasurement extends PowerPointCalibrationProbe {
  readonly boxBoundsPt: PowerPointTextBoundsPt;
  readonly textBoundsPt: PowerPointTextBoundsPt;
  readonly lineCount: number | null;
}

export interface PowerPointCalibrationProfile {
  readonly fontFamily: string;
  readonly fontWeight: number;
  readonly fontSizePt: number;
  readonly lineMode: string;
  readonly horizontalAlignment: string;
  readonly widthMode: string;
  readonly sampleCount: number;
  readonly widthScale: number;
  readonly baselineOffsetPt: number;
  readonly lineBoxHeightPt: number;
}

const SINGLE_LINE_SAMPLE = "PowerPoint 가나다 ABC 123";
const MULTILINE_SAMPLE = "PowerPoint 가나다 ABC\n줄 간격 측정 123";

/**
 * Produces the deliberately small, deterministic matrix used by the Desktop
 * probe. A profile key contains every dimension that changes PowerPoint text
 * shaping; callers must not collapse it into one global correction.
 */
export function buildPowerPointCalibrationProbes(): PowerPointCalibrationProbe[] {
  const result: PowerPointCalibrationProbe[] = [];
  for (const weight of POWERPOINT_CALIBRATION_FONT_WEIGHTS) {
    for (const fontSizePt of POWERPOINT_CALIBRATION_FONT_SIZES_PT) {
      for (const lineMode of POWERPOINT_CALIBRATION_LINE_MODES) {
        for (const horizontalAlignment of POWERPOINT_CALIBRATION_ALIGNMENTS) {
          for (const widthMode of POWERPOINT_CALIBRATION_WIDTH_MODES) {
            result.push({
              id: [
                "noto-sans-kr",
                weight.label,
                `${fontSizePt}pt`,
                lineMode,
                horizontalAlignment,
                widthMode,
              ].join("-"),
              fontFamily: POWERPOINT_CALIBRATION_FONT_FAMILY,
              fontWeight: weight.value,
              fontSizePt,
              lineMode,
              horizontalAlignment,
              widthMode,
              text: lineMode === "single" ? SINGLE_LINE_SAMPLE : MULTILINE_SAMPLE,
            });
          }
        }
      }
    }
  }
  return result;
}

function median(values: readonly number[]): number {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
}

function rounded(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

function profileKey(measurement: PowerPointCalibrationMeasurement): string {
  return [
    measurement.fontFamily,
    measurement.fontWeight,
    measurement.fontSizePt,
    measurement.lineMode,
    measurement.horizontalAlignment,
    measurement.widthMode,
  ].join("\0");
}

/**
 * Converts raw COM geometry into data-only calibration profiles. Median
 * aggregation avoids turning one noisy Desktop paint into an exporter rule.
 */
export function derivePowerPointCalibrationProfiles(
  measurements: readonly PowerPointCalibrationMeasurement[]
): PowerPointCalibrationProfile[] {
  const groups = new Map<string, PowerPointCalibrationMeasurement[]>();
  for (const measurement of measurements) {
    if (
      ![
        measurement.boxBoundsPt.left,
        measurement.boxBoundsPt.top,
        measurement.boxBoundsPt.width,
        measurement.boxBoundsPt.height,
        measurement.textBoundsPt.left,
        measurement.textBoundsPt.top,
        measurement.textBoundsPt.width,
        measurement.textBoundsPt.height,
      ].every(Number.isFinite) ||
      measurement.boxBoundsPt.width <= 0 ||
      measurement.textBoundsPt.width < 0 ||
      measurement.textBoundsPt.height < 0
    ) {
      continue;
    }
    const key = profileKey(measurement);
    const group = groups.get(key) ?? [];
    group.push(measurement);
    groups.set(key, group);
  }

  return [...groups.values()].map((group) => {
    const first = group[0];
    const widths = group.map(
      (item) => item.textBoundsPt.width / item.boxBoundsPt.width
    );
    const baselines = group.map(
      (item) => item.textBoundsPt.top - item.boxBoundsPt.top
    );
    const lineBoxes = group.map((item) => {
      const lines = item.lineCount && item.lineCount > 0 ? item.lineCount : 1;
      return item.textBoundsPt.height / lines;
    });
    return {
      fontFamily: first.fontFamily,
      fontWeight: first.fontWeight,
      fontSizePt: first.fontSizePt,
      lineMode: first.lineMode,
      horizontalAlignment: first.horizontalAlignment,
      widthMode: first.widthMode,
      sampleCount: group.length,
      widthScale: rounded(median(widths)),
      baselineOffsetPt: rounded(median(baselines)),
      lineBoxHeightPt: rounded(median(lineBoxes)),
    };
  });
}
