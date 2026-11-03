/**
 * A custom SVG canvas: millimetres (two decimals), or an exact slide size in
 * EMU for a source the millimetre form cannot express (a 302.992 mm template).
 */
export type SvgCanvasSelection =
  | { width_mm: number; height_mm: number }
  | { width_emu: number; height_emu: number };

const EMU_PER_MM = 36000;

/** Millimetres for display, rounded to the 0.01 mm the picker shows. */
export function svgCanvasMillimetres(canvas: SvgCanvasSelection): { width: number; height: number } {
  if ("width_emu" in canvas) {
    return { width: Math.round(canvas.width_emu / EMU_PER_MM * 100) / 100, height: Math.round(canvas.height_emu / EMU_PER_MM * 100) / 100 };
  }
  return { width: canvas.width_mm, height: canvas.height_mm };
}
