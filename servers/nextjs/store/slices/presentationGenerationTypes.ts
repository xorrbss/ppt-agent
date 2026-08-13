/** Object/array container addressed by a dotted+indexed content data path. */
export type PathContainer = Record<string | number, unknown>;

/** Structural chart payload used by the editor state. */
export interface SlideChart {
  type?: "bar" | "line" | "area";
  data: Array<{ name?: string; value?: number }>;
}

/** Template-family-neutral slide record used only while re-indexing. */
export type SlideRecord = Record<string, unknown>;
