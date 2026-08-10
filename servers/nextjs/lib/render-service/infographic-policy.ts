export const INFOGRAPHIC_POLICY_VERSION = "a6552331" as const;

export type InfographicKind = "progress_bar" | "gauge";

export interface InfographicViewModel {
  readonly policy_version: typeof INFOGRAPHIC_POLICY_VERSION;
  readonly kind: InfographicKind;
  readonly ratio: number;
  readonly highlight_color: string;
  readonly base_color: string;
  /** a6552331 deliberately removes renderer-specific numeric labels. */
  readonly show_numeric_label: false;
  /** A centered slim track is shared by preview/export renderers. */
  readonly progress_track_height: number;
}

export interface InfographicViewModelInput {
  readonly kind: InfographicKind;
  readonly value: number;
  readonly min_value: number;
  readonly max_value: number;
  readonly highlight_color: string;
  readonly base_color: string;
  readonly frame_height?: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function createInfographicViewModel(
  input: InfographicViewModelInput,
): InfographicViewModel {
  if (
    !Number.isFinite(input.value) ||
    !Number.isFinite(input.min_value) ||
    !Number.isFinite(input.max_value) ||
    input.max_value < input.min_value
  ) {
    throw new Error("render_infographic_invalid_range");
  }
  const value = clamp(input.value, input.min_value, input.max_value);
  const ratio =
    input.max_value === input.min_value
      ? 0
      : (value - input.min_value) / (input.max_value - input.min_value);
  const frameHeight = Number.isFinite(input.frame_height)
    ? Number(input.frame_height)
    : 40;
  return Object.freeze({
    policy_version: INFOGRAPHIC_POLICY_VERSION,
    kind: input.kind,
    ratio,
    highlight_color: input.highlight_color,
    base_color: input.base_color,
    show_numeric_label: false,
    progress_track_height: clamp(Math.round(frameHeight * 0.18), 6, 10),
  });
}
