export interface CanvasCoreBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CanvasCoreGuide {
  orientation: "vertical" | "horizontal";
  position: number;
  start: number;
  end: number;
}

type AlignmentAnchor = "start" | "center" | "end";

interface AlignmentTarget {
  anchor: AlignmentAnchor;
  bounds: CanvasCoreBounds | null;
  kind: "element" | "frame";
  value: number;
}

interface AlignmentCandidate extends AlignmentTarget {
  delta: number;
  distance: number;
  movingAnchor: AlignmentAnchor;
}

export interface CanvasCoreAlignmentTargets {
  horizontal: AlignmentTarget[];
  vertical: AlignmentTarget[];
}

const GUIDE_EXTENSION = 24;
const DISTANCE_EPSILON = 0.001;

function axisAnchors(bounds: CanvasCoreBounds, axis: "x" | "y") {
  const start = axis === "x" ? bounds.x : bounds.y;
  const size = axis === "x" ? bounds.width : bounds.height;
  return [
    { anchor: "start" as const, value: start },
    { anchor: "center" as const, value: start + size / 2 },
    { anchor: "end" as const, value: start + size },
  ];
}

function targetsForBounds(
  bounds: CanvasCoreBounds,
  axis: "x" | "y",
  kind: AlignmentTarget["kind"],
): AlignmentTarget[] {
  return axisAnchors(bounds, axis).map(({ anchor, value }) => ({
    anchor,
    bounds: kind === "element" ? bounds : null,
    kind,
    value,
  }));
}

export function createCanvasCoreAlignmentTargets(
  alignmentFrame: CanvasCoreBounds,
  elementBounds: readonly CanvasCoreBounds[],
): CanvasCoreAlignmentTargets {
  return {
    vertical: [
      ...targetsForBounds(alignmentFrame, "x", "frame"),
      ...elementBounds.flatMap((bounds) => targetsForBounds(bounds, "x", "element")),
    ],
    horizontal: [
      ...targetsForBounds(alignmentFrame, "y", "frame"),
      ...elementBounds.flatMap((bounds) => targetsForBounds(bounds, "y", "element")),
    ],
  };
}

function anchorsCanAlign(movingAnchor: AlignmentAnchor, target: AlignmentTarget) {
  if (target.kind === "frame") return movingAnchor === target.anchor;
  const includesCenter = movingAnchor === "center" || target.anchor === "center";
  return includesCenter
    ? movingAnchor === "center" && target.anchor === "center"
    : true;
}

function candidatePriority(candidate: AlignmentCandidate) {
  if (candidate.kind === "frame" && candidate.anchor === "center" && candidate.movingAnchor === "center") return 0;
  if (candidate.anchor === "center" && candidate.movingAnchor === "center") return 1;
  if (candidate.anchor === candidate.movingAnchor) return 2;
  return 3;
}

function isBetterCandidate(candidate: AlignmentCandidate, current: AlignmentCandidate | null) {
  if (!current) return true;
  if (candidate.distance < current.distance - DISTANCE_EPSILON) return true;
  if (candidate.distance > current.distance + DISTANCE_EPSILON) return false;
  return candidatePriority(candidate) < candidatePriority(current);
}

function closestAlignment(
  movingBounds: CanvasCoreBounds,
  targets: AlignmentTarget[],
  axis: "x" | "y",
  threshold: number,
) {
  let closest: AlignmentCandidate | null = null;
  for (const moving of axisAnchors(movingBounds, axis)) {
    for (const target of targets) {
      if (!anchorsCanAlign(moving.anchor, target)) continue;
      const delta = target.value - moving.value;
      const candidate = { ...target, delta, distance: Math.abs(delta), movingAnchor: moving.anchor };
      if (candidate.distance <= threshold && isBetterCandidate(candidate, closest)) closest = candidate;
    }
  }
  return closest;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function guideForCandidate(
  candidate: AlignmentCandidate,
  snappedBounds: CanvasCoreBounds,
  guideBounds: CanvasCoreBounds,
  axis: "x" | "y",
  axisTargets: AlignmentTarget[],
): CanvasCoreGuide {
  if (candidate.kind === "frame" || !candidate.bounds) {
    return axis === "x"
      ? { orientation: "vertical", position: candidate.value, start: guideBounds.y, end: guideBounds.y + guideBounds.height }
      : { orientation: "horizontal", position: candidate.value, start: guideBounds.x, end: guideBounds.x + guideBounds.width };
  }
  const alignedBounds = Array.from(new Set(axisTargets.flatMap((target) =>
    target.kind === "element" && target.bounds && Math.abs(target.value - candidate.value) <= DISTANCE_EPSILON
      ? [target.bounds]
      : [],
  )));
  const boxes = [snappedBounds, ...alignedBounds];
  if (axis === "x") {
    return {
      orientation: "vertical",
      position: candidate.value,
      start: clamp(Math.min(...boxes.map((bounds) => bounds.y)) - GUIDE_EXTENSION, guideBounds.y, guideBounds.y + guideBounds.height),
      end: clamp(Math.max(...boxes.map((bounds) => bounds.y + bounds.height)) + GUIDE_EXTENSION, guideBounds.y, guideBounds.y + guideBounds.height),
    };
  }
  return {
    orientation: "horizontal",
    position: candidate.value,
    start: clamp(Math.min(...boxes.map((bounds) => bounds.x)) - GUIDE_EXTENSION, guideBounds.x, guideBounds.x + guideBounds.width),
    end: clamp(Math.max(...boxes.map((bounds) => bounds.x + bounds.width)) + GUIDE_EXTENSION, guideBounds.x, guideBounds.x + guideBounds.width),
  };
}

export function snapCanvasCoreBounds({
  movingBounds,
  guideBounds,
  targets,
  threshold,
}: {
  movingBounds: CanvasCoreBounds;
  guideBounds: CanvasCoreBounds;
  targets: CanvasCoreAlignmentTargets;
  threshold: number;
}) {
  const safeThreshold = Math.max(0, threshold);
  const vertical = closestAlignment(movingBounds, targets.vertical, "x", safeThreshold);
  const horizontal = closestAlignment(movingBounds, targets.horizontal, "y", safeThreshold);
  const position = {
    x: movingBounds.x + (vertical?.delta ?? 0),
    y: movingBounds.y + (horizontal?.delta ?? 0),
  };
  const snappedBounds = { ...movingBounds, ...position };
  return {
    position,
    guides: [
      ...(vertical ? [guideForCandidate(vertical, snappedBounds, guideBounds, "x", targets.vertical)] : []),
      ...(horizontal ? [guideForCandidate(horizontal, snappedBounds, guideBounds, "y", targets.horizontal)] : []),
    ],
  };
}
