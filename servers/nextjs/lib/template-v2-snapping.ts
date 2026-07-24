export const TEMPLATE_V2_SNAP_GRID = 8;
export const TEMPLATE_V2_SNAP_THRESHOLD = 3;

function snapAxis(value: number, grid: number, threshold: number): number {
  if (!Number.isFinite(value) || grid <= 0 || threshold < 0) return value;
  const candidate = Math.round(value / grid) * grid;
  return Math.abs(candidate - value) <= threshold ? candidate : value;
}

export function snapTemplateV2Position(
  position: { x: number; y: number },
  grid = TEMPLATE_V2_SNAP_GRID,
  threshold = TEMPLATE_V2_SNAP_THRESHOLD
) {
  return {
    x: snapAxis(position.x, grid, threshold),
    y: snapAxis(position.y, grid, threshold),
  };
}
