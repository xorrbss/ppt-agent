/**
 * Flagged-slide badges for the editor.
 *
 * The authored pipeline's terminal fallback (D7) and author-stage rescue
 * publish a remade-or-placeholder slide instead of failing the deck, and
 * record what they did in the quality-gate receipt the backend persists on
 * ``presentation.theme.authoredQualityGate.flaggedSlides``. That receipt
 * travels with ``GET /presentation/{id}``, so no extra request is needed;
 * this module turns it into a slide-id keyed map the sidebar and the slide
 * itself can read.
 *
 * Receipt entries are keyed by the generation-time slide index. The map is
 * built once, when the deck loads, and keyed by slide id so a badge survives
 * an in-session reorder.
 */

export type FlaggedSlideStage = "author" | "gate";

export interface AuthoredFlaggedSlideEntry {
  index: number;
  reason: string;
  remakeAttempted?: boolean;
  remakeOutcome?: string;
  remakeCount?: number;
  repeated?: boolean;
}

export interface FlaggedSlideBadge {
  slideId: string | null;
  /** Generation-time index the receipt recorded (0-based). */
  slideIndex: number;
  reason: string;
  stage: FlaggedSlideStage;
  /** Defect categories parsed from a ``composition_safety:`` reason. */
  categories: string[];
  remakeAttempted: boolean;
  remakeOutcome: string | null;
}

export type FlaggedSlideMap = Record<string, FlaggedSlideBadge>;

const AUTHOR_REASON_PREFIX = "author:";
const GATE_REASON_PREFIX = "composition_safety:";

type SlideLike = { id?: string | null; index?: number | null };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Map key for a slide: its id, or its list position when it has none. */
export function flaggedSlideKey(slide: SlideLike | null | undefined, position: number): string {
  const id = slide?.id;
  return typeof id === "string" && id.length > 0 ? id : `index:${position}`;
}

/** Read the receipt's flagged entries off a presentation theme, defensively. */
export function readFlaggedSlideEntries(theme: unknown): AuthoredFlaggedSlideEntry[] {
  if (!isRecord(theme)) return [];
  const gate = theme.authoredQualityGate;
  if (!isRecord(gate) || !Array.isArray(gate.flaggedSlides)) return [];
  const entries: AuthoredFlaggedSlideEntry[] = [];
  for (const raw of gate.flaggedSlides) {
    if (!isRecord(raw)) continue;
    const index = raw.index;
    const reason = raw.reason;
    if (typeof index !== "number" || !Number.isInteger(index) || index < 0) continue;
    if (typeof reason !== "string" || reason.length === 0) continue;
    entries.push({
      index,
      reason,
      remakeAttempted: raw.remakeAttempted === true,
      remakeOutcome: typeof raw.remakeOutcome === "string" ? raw.remakeOutcome : undefined,
      remakeCount: typeof raw.remakeCount === "number" ? raw.remakeCount : undefined,
      repeated: typeof raw.repeated === "boolean" ? raw.repeated : undefined,
    });
  }
  return entries;
}

export function flaggedSlideStage(reason: string): FlaggedSlideStage {
  return reason.startsWith(AUTHOR_REASON_PREFIX) ? "author" : "gate";
}

export function flaggedSlideCategories(reason: string): string[] {
  if (!reason.startsWith(GATE_REASON_PREFIX)) return [];
  return reason
    .slice(GATE_REASON_PREFIX.length)
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0 && item !== "unknown");
}

function toBadge(entry: AuthoredFlaggedSlideEntry, slide: SlideLike | undefined): FlaggedSlideBadge {
  return {
    slideId: typeof slide?.id === "string" && slide.id.length > 0 ? slide.id : null,
    slideIndex: entry.index,
    reason: entry.reason,
    stage: flaggedSlideStage(entry.reason),
    categories: flaggedSlideCategories(entry.reason),
    remakeAttempted: entry.remakeAttempted === true,
    remakeOutcome: entry.remakeOutcome ?? null,
  };
}

/**
 * Build the slide-keyed badge map from a freshly loaded presentation. Slides
 * are matched by their ``index`` field first (the order the receipt used) and
 * by list position when a slide carries no index.
 */
export function deriveFlaggedSlideMap(presentation: {
  theme?: unknown;
  slides?: unknown;
} | null | undefined): FlaggedSlideMap {
  const map: FlaggedSlideMap = {};
  if (!presentation) return map;
  const entries = readFlaggedSlideEntries(presentation.theme);
  if (entries.length === 0) return map;
  const slides: SlideLike[] = Array.isArray(presentation.slides)
    ? presentation.slides.filter(isRecord)
    : [];
  for (const entry of entries) {
    const byIndex = slides.findIndex((slide) => slide.index === entry.index);
    const position = byIndex >= 0 ? byIndex : entry.index;
    const slide = slides[position];
    map[flaggedSlideKey(slide, position)] = toBadge(entry, slide);
  }
  return map;
}

export function flaggedSlideCount(map: FlaggedSlideMap | null | undefined): number {
  return map ? Object.keys(map).length : 0;
}

type FlaggedSlideReasonKey =
  | "reason.author"
  | "reason.gate"
  | "reason.gateUnknown"
  | "reason.other";

/**
 * The subset of ``useTranslations("flaggedSlides")`` this module calls. Keyed
 * by the literal union so next-intl's typed translator (whose key type is a
 * superset) and a plain test stub both satisfy it.
 */
type Translate = (
  key: FlaggedSlideReasonKey,
  values?: Record<string, string | number>,
) => string;

/** Human-readable reason for the tooltip, in the ``flaggedSlides`` namespace. */
export function describeFlaggedSlideReason(t: Translate, badge: FlaggedSlideBadge): string {
  if (badge.stage === "author") return t("reason.author");
  if (badge.reason.startsWith(GATE_REASON_PREFIX)) {
    return badge.categories.length > 0
      ? t("reason.gate", { categories: badge.categories.join(", ") })
      : t("reason.gateUnknown");
  }
  return t("reason.other", { reason: badge.reason });
}
