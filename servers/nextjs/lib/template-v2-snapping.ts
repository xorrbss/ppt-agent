export const TEMPLATE_V2_SNAP_GRID = 8;
export const TEMPLATE_V2_SNAP_THRESHOLD = 3;

export interface TemplateV2Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface TemplateV2Guide {
  orientation: "vertical" | "horizontal";
  position: number;
  start: number;
  end: number;
}

export interface TemplateV2SnapResult {
  position: { x: number; y: number };
  guides: TemplateV2Guide[];
}

type SnapAxis = "x" | "y";

interface AxisSnap {
  delta: number;
  position: number;
  target: TemplateV2Bounds;
}

function snapAxisToGrid(value: number, grid: number, threshold: number): number {
  if (!Number.isFinite(value) || grid <= 0 || threshold < 0) return value;
  const candidate = Math.round(value / grid) * grid;
  return Math.abs(candidate - value) <= threshold ? candidate : value;
}

// Leading edge, center, and trailing edge of a box on one axis. Aligning any of
// the three against any of a target's three is what an alignment guide means.
function axisEdges(bounds: TemplateV2Bounds, axis: SnapAxis): number[] {
  const start = axis === "x" ? bounds.x : bounds.y;
  const extent = axis === "x" ? bounds.width : bounds.height;
  return [start, start + extent / 2, start + extent];
}

function bestAxisSnap(
  moving: TemplateV2Bounds,
  targets: TemplateV2Bounds[],
  axis: SnapAxis,
  threshold: number
): AxisSnap | null {
  let best: AxisSnap | null = null;
  for (const movingEdge of axisEdges(moving, axis)) {
    if (!Number.isFinite(movingEdge)) return null;
    for (const target of targets) {
      for (const targetEdge of axisEdges(target, axis)) {
        const delta = targetEdge - movingEdge;
        if (!Number.isFinite(delta) || Math.abs(delta) > threshold) continue;
        if (!best || Math.abs(delta) < Math.abs(best.delta)) {
          best = { delta, position: targetEdge, target };
        }
      }
    }
  }
  return best;
}

function axisGuide(
  snap: AxisSnap,
  moving: TemplateV2Bounds,
  axis: SnapAxis
): TemplateV2Guide {
  const [movingStart, movingExtent, targetStart, targetExtent] =
    axis === "x"
      ? [moving.y, moving.height, snap.target.y, snap.target.height]
      : [moving.x, moving.width, snap.target.x, snap.target.width];
  return {
    orientation: axis === "x" ? "vertical" : "horizontal",
    position: snap.position,
    start: Math.min(movingStart, targetStart),
    end: Math.max(movingStart + movingExtent, targetStart + targetExtent),
  };
}

/**
 * Snaps a dragged box against the boxes it should line up with.
 *
 * Each axis prefers the closest edge/center alignment within the threshold and
 * reports the guide to draw for it; an axis with no alignment in reach falls
 * back to the grid, so free movement keeps the previous snapping behaviour.
 */
export function snapTemplateV2Bounds(
  moving: TemplateV2Bounds,
  targets: TemplateV2Bounds[] = [],
  threshold = TEMPLATE_V2_SNAP_THRESHOLD,
  grid = TEMPLATE_V2_SNAP_GRID
): TemplateV2SnapResult {
  const snaps: Record<SnapAxis, AxisSnap | null> = {
    x: bestAxisSnap(moving, targets, "x", threshold),
    y: bestAxisSnap(moving, targets, "y", threshold),
  };
  const position = {
    x: snaps.x ? moving.x + snaps.x.delta : snapAxisToGrid(moving.x, grid, threshold),
    y: snaps.y ? moving.y + snaps.y.delta : snapAxisToGrid(moving.y, grid, threshold),
  };
  const snapped: TemplateV2Bounds = { ...moving, ...position };
  const guides: TemplateV2Guide[] = [];
  if (snaps.x) guides.push(axisGuide(snaps.x, snapped, "x"));
  if (snaps.y) guides.push(axisGuide(snaps.y, snapped, "y"));
  return { position, guides };
}

// The subset of a Konva node the canvas needs to derive a target box.
export interface TemplateV2BoundsNode {
  x(): number;
  y(): number;
  width(): number;
  height(): number;
}

/**
 * Boxes a dragged element can align against: the sibling nodes that are not part
 * of the drag, plus the slide frame. Reading the live nodes keeps guides
 * consistent with what is drawn, including plan-backed element types whose frame
 * does not come from `position`/`size`.
 */
export function templateV2GuideTargets(
  nodes: Map<string, TemplateV2BoundsNode>,
  keys: string[],
  excluded: ReadonlySet<string>,
  slide: TemplateV2Bounds
): TemplateV2Bounds[] {
  const targets = keys.flatMap((key) => {
    const node = excluded.has(key) ? undefined : nodes.get(key);
    return node
      ? [
          {
            x: node.x(),
            y: node.y(),
            width: node.width(),
            height: node.height(),
          },
        ]
      : [];
  });
  return [...targets, slide];
}

export function sameTemplateV2Guides(
  left: TemplateV2Guide[],
  right: TemplateV2Guide[]
): boolean {
  return (
    left.length === right.length &&
    left.every(
      (guide, index) =>
        guide.orientation === right[index].orientation &&
        guide.position === right[index].position &&
        guide.start === right[index].start &&
        guide.end === right[index].end
    )
  );
}
