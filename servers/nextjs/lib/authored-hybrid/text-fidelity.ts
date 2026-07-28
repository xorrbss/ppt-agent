import type {
  AuthoredHybridRect,
  AuthoredHybridTextPayload,
} from "./schema.ts";

export const AUTHORED_HYBRID_TEXT_PROFILES = [
  "display-title",
  "multiline-title",
  "body",
  "centered-label",
  "compact-caption",
  "table-cell",
  "mixed-weight",
] as const;

export type AuthoredHybridTextProfile =
  (typeof AUTHORED_HYBRID_TEXT_PROFILES)[number];

export type AuthoredHybridTextFidelityMode =
  | "editable"
  | "powerpoint-calibrated";

export interface NativeTextTransform {
  /** Applied equally to all four captured text-box insets. */
  insetPt: number;
  lineSpacingPt: number;
  /** Fractional width change: 0.004 is +0.4%. */
  widthScale: number;
  verticalPt: number;
}

export interface NativeTextFidelitySelection {
  profile: AuthoredHybridTextProfile;
  transform: NativeTextTransform;
  candidateIndex: number;
}

const ZERO_TRANSFORM: NativeTextTransform = {
  insetPt: 0,
  lineSpacingPt: 0,
  widthScale: 0,
  verticalPt: 0,
};

const PROFILE_TARGETS: Record<
  AuthoredHybridTextProfile,
  NativeTextTransform
> = {
  "display-title": ZERO_TRANSFORM,
  "multiline-title": ZERO_TRANSFORM,
  body: ZERO_TRANSFORM,
  "centered-label": ZERO_TRANSFORM,
  // The PowerPoint/LibreOffice A-B corpus showed that a universal caption
  // offset improves many dense slides but creates visible regressions when a
  // slide is almost entirely 9pt captions. Keep the native baseline until a
  // geometry-conditioned caption candidate has positive calibration evidence.
  "compact-caption": ZERO_TRANSFORM,
  "table-cell": ZERO_TRANSFORM,
  "mixed-weight": ZERO_TRANSFORM,
};

function distinctRunWeights(text: AuthoredHybridTextPayload): Set<number> {
  return new Set(
    text.runs
      .filter((run) => run.text.replace(/\s/g, "").length > 0)
      .map((run) => (run.style.bold || run.style.fontWeight >= 600 ? 700 : 400))
  );
}

function isTableCellLayout(text: AuthoredHybridTextPayload): boolean {
  const layout = text.layout;
  if (!layout) return false;
  const asymmetricPadding =
    Math.abs(layout.paddingPx.left - layout.paddingPx.right) >= 0.5 ||
    Math.abs(layout.paddingPx.top - layout.paddingPx.bottom) >= 0.5;
  return (
    (layout.display.includes("table-cell") ||
      (layout.display.includes("flex") && asymmetricPadding)) &&
    text.style.fontSizePt <= 18
  );
}

/**
 * Classify editable text only from captured semantic and computed layout facts.
 * Slide index, element id, and absolute slide coordinates are intentionally not
 * accepted, keeping the rule portable across templates.
 */
export function resolveAuthoredHybridTextProfile(
  text: AuthoredHybridTextPayload
): AuthoredHybridTextProfile {
  const lineCount = Math.max(1, text.layout?.lineCount ?? text.paragraphs.length);
  const mixedWeight = distinctRunWeights(text).size > 1;

  if (isTableCellLayout(text)) return "table-cell";
  if (mixedWeight) return "mixed-weight";
  if (
    text.role === "title" &&
    text.style.fontSizePt >= 24 &&
    lineCount >= 2
  ) {
    return "multiline-title";
  }
  if (
    text.role === "title" &&
    text.style.fontSizePt >= 24 &&
    lineCount === 1
  ) {
    return "display-title";
  }
  if (
    text.style.horizontalAlignment === "center" &&
    text.style.fontSizePt <= 18 &&
    lineCount <= 2
  ) {
    return "centered-label";
  }
  if (
    text.role === "caption" ||
    (text.style.fontSizePt <= 10 && lineCount <= 2)
  ) {
    return "compact-caption";
  }
  return "body";
}

function candidateKey(candidate: NativeTextTransform): string {
  return [
    candidate.insetPt,
    candidate.lineSpacingPt,
    candidate.widthScale,
    candidate.verticalPt,
  ].join(":");
}

/**
 * A bounded coordinate search plus one combined profile target. Each non-zero
 * value stays inside the PowerPoint calibration envelope:
 * inset 0.25–0.75pt, line spacing 0.25–1pt, width 0.2–0.8%, vertical 0.25–0.5pt.
 */
export function nativeTextTransformCandidates(
  profile: AuthoredHybridTextProfile
): readonly NativeTextTransform[] {
  const candidates: NativeTextTransform[] = [{ ...ZERO_TRANSFORM }];
  const add = (patch: Partial<NativeTextTransform>) => {
    candidates.push({ ...ZERO_TRANSFORM, ...patch });
  };
  for (const insetPt of [-0.75, -0.5, -0.25, 0.25, 0.5, 0.75]) {
    add({ insetPt });
  }
  for (const lineSpacingPt of [-1, -0.75, -0.5, -0.25, 0.25, 0.5, 0.75, 1]) {
    add({ lineSpacingPt });
  }
  for (const widthScale of [
    -0.008, -0.006, -0.004, -0.002, 0.002, 0.004, 0.006, 0.008,
  ]) {
    add({ widthScale });
  }
  for (const verticalPt of [-0.5, -0.25, 0.25, 0.5]) {
    add({ verticalPt });
  }
  candidates.push({ ...PROFILE_TARGETS[profile] });
  return candidates.filter(
    (candidate, index) =>
      candidates.findIndex((other) => candidateKey(other) === candidateKey(candidate)) ===
      index
  );
}

function targetDistance(
  candidate: NativeTextTransform,
  target: NativeTextTransform
): number {
  return (
    Math.abs(candidate.insetPt - target.insetPt) / 0.25 +
    Math.abs(candidate.lineSpacingPt - target.lineSpacingPt) / 0.25 +
    Math.abs(candidate.widthScale - target.widthScale) / 0.002 +
    Math.abs(candidate.verticalPt - target.verticalPt) / 0.25
  );
}

function regressionPenalty(
  text: AuthoredHybridTextPayload,
  bounds: AuthoredHybridRect,
  candidate: NativeTextTransform
): number {
  const layout = text.layout;
  if (!layout) return 0;
  const content = layout.contentBounds.px;
  const painted = layout.paintedTextBounds?.px;
  const insetPixels = candidate.insetPt / 0.75;
  const availableWidth =
    content.width * (1 + candidate.widthScale) - insetPixels * 2;
  const lineCount = Math.max(1, layout.lineCount);
  const availableHeight =
    content.height -
    insetPixels * 2 -
    candidate.verticalPt / 0.75;
  const requiredHeight =
    lineCount *
    Math.max(
      text.style.fontSizePt / 0.75,
      (text.style.lineHeight.points + candidate.lineSpacingPt) / 0.75
    );
  let penalty = 0;
  if (availableWidth <= 0 || availableHeight <= 0) return 10_000;
  if (painted && painted.width > availableWidth + 0.5) {
    penalty += 500 + (painted.width - availableWidth) * 25;
  }
  if (requiredHeight > availableHeight + Math.max(2, lineCount)) {
    penalty += 500 + (requiredHeight - availableHeight) * 25;
  }
  // Width contraction on a browser-confirmed single line is the highest-risk
  // source of a new PowerPoint wrap.
  if (layout.singleLine && candidate.widthScale < 0) penalty += 1_000;
  if (bounds.width * (1 + candidate.widthScale) <= 0) penalty += 10_000;
  return penalty;
}

export function selectNativeTextFidelity(
  text: AuthoredHybridTextPayload,
  bounds: AuthoredHybridRect
): NativeTextFidelitySelection {
  const profile = resolveAuthoredHybridTextProfile(text);
  const target = {
    ...PROFILE_TARGETS[profile],
    // Content-sized labels already follow their painted glyph width. Use the
    // smallest width candidate while fixed boxes retain profile calibration.
    ...(text.layout?.widthMode === "content" &&
    PROFILE_TARGETS[profile].widthScale > 0.002
      ? { widthScale: 0.002 }
      : {}),
  };
  const profileCandidates = nativeTextTransformCandidates(profile);
  const candidates = profileCandidates.some(
    (candidate) => candidateKey(candidate) === candidateKey(target)
  )
    ? profileCandidates
    : [...profileCandidates, target];
  let candidateIndex = 0;
  let bestScore = Number.POSITIVE_INFINITY;
  candidates.forEach((candidate, index) => {
    const score =
      targetDistance(candidate, target) +
      regressionPenalty(text, bounds, candidate);
    if (score < bestScore) {
      bestScore = score;
      candidateIndex = index;
    }
  });
  return {
    profile,
    transform: candidates[candidateIndex],
    candidateIndex,
  };
}

export function applyNativeTextBoundsTransform(
  bounds: AuthoredHybridRect,
  horizontalAlignment: AuthoredHybridTextPayload["style"]["horizontalAlignment"],
  transform: NativeTextTransform
): AuthoredHybridRect {
  const width = bounds.width * (1 + transform.widthScale);
  const widthDelta = width - bounds.width;
  const x =
    horizontalAlignment === "center"
      ? bounds.x - widthDelta / 2
      : horizontalAlignment === "right"
        ? bounds.x - widthDelta
        : bounds.x;
  return {
    x,
    y: bounds.y + transform.verticalPt / 0.75,
    width,
    height: bounds.height,
  };
}
