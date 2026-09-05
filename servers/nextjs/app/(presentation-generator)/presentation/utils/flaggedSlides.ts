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
 * Receipt entries carry the id of the slide they were generated for
 * (``slideId``, backfilled by ``authored_presentation_publication.py`` once
 * the slide rows exist). The map is built once, when the deck loads, and
 * keyed by that id so a badge stays on its slide across an in-session
 * reorder -- ``SidePanel.tsx``'s drag handler renumbers every slide's
 * ``index`` field and ``useAutoSave`` persists it, so the receipt's
 * generation-time ``index`` can no longer be trusted to name the same slide
 * after a reorder. A receipt persisted before ``slideId`` existed has no id
 * to fall back on; for that legacy shape only, this still matches by
 * ``index``, which keeps working until the deck is reordered and then
 * mismatches exactly as before -- the deck must be regenerated (or its
 * receipt re-derived server-side) to pick up a stable id.
 */

export type FlaggedSlideStage = "author" | "gate";

export interface AuthoredFlaggedSlideEntry {
  index: number;
  /** Id of the slide this entry was generated for. Absent on legacy receipts. */
  slideId?: string;
  reason: string;
  /**
   * Every distinct reason recorded for this slide, when more than one stage
   * flagged it (author-stage rescue and the terminal gate fallback can both
   * flag the same slide). Absent when there is only the one ``reason``.
   */
  reasons?: string[];
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
  /** Every distinct reason recorded for this slide (at least ``[reason]``). */
  reasons: string[];
  stage: FlaggedSlideStage;
  /** Defect categories parsed from a ``composition_safety:`` reason. */
  categories: string[];
  remakeAttempted: boolean;
  remakeOutcome: string | null;
}

export type FlaggedSlideMap = Record<string, FlaggedSlideBadge>;

const AUTHOR_REASON_PREFIX = "author:";
const GATE_REASON_PREFIX = "composition_safety:";

/**
 * Raw category tokens the backend can put after ``composition_safety:``.
 * ``services/authored_composition_policy.py``'s ``blocking_safety_issues``
 * emits ``overflow``/``clipping``/``off_canvas``/``overlap``;
 * ``services/authored_terminal_slide_policy.py`` emits
 * ``repair_budget_exhausted``/``gate_repair_not_admitted`` as the whole
 * category. This is every value either module produces -- do not add a
 * label here for a category neither one emits.
 */
const CATEGORY_LABEL_KEYS: Record<string, FlaggedSlideCategoryKey> = {
  overflow: "category.overflow",
  clipping: "category.clipping",
  off_canvas: "category.offCanvas",
  overlap: "category.overlap",
  repair_budget_exhausted: "category.repairBudgetExhausted",
  gate_repair_not_admitted: "category.gateRepairNotAdmitted",
};

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
    const reasons = Array.isArray(raw.reasons)
      ? raw.reasons.filter(
          (item): item is string => typeof item === "string" && item.length > 0,
        )
      : undefined;
    entries.push({
      index,
      slideId: typeof raw.slideId === "string" && raw.slideId.length > 0 ? raw.slideId : undefined,
      reason,
      reasons: reasons && reasons.length > 0 ? reasons : undefined,
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
    reasons: entry.reasons && entry.reasons.length > 0 ? entry.reasons : [entry.reason],
    stage: flaggedSlideStage(entry.reason),
    categories: flaggedSlideCategories(entry.reason),
    remakeAttempted: entry.remakeAttempted === true,
    remakeOutcome: entry.remakeOutcome ?? null,
  };
}

/**
 * Build the slide-keyed badge map from a freshly loaded presentation.
 *
 * A receipt entry is matched to the slide it names by ``slideId`` first,
 * which survives a reorder because it never changes; only a legacy entry
 * with no id falls back to matching the generation-time ``index`` field (or,
 * failing that, list position) -- see the module docstring for that path's
 * remaining failure mode.
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
    const byId = entry.slideId
      ? slides.findIndex((slide) => slide.id === entry.slideId)
      : -1;
    const byIndex =
      byId >= 0 ? byId : slides.findIndex((slide) => slide.index === entry.index);
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

type FlaggedSlideCategoryKey =
  | "category.overflow"
  | "category.clipping"
  | "category.offCanvas"
  | "category.overlap"
  | "category.repairBudgetExhausted"
  | "category.gateRepairNotAdmitted";

/**
 * The subset of ``useTranslations("flaggedSlides")`` this module calls. Keyed
 * by the literal union so next-intl's typed translator (whose key type is a
 * superset) and a plain test stub both satisfy it.
 */
type Translate = (
  key: FlaggedSlideReasonKey | FlaggedSlideCategoryKey,
  values?: Record<string, string | number>,
) => string;

/**
 * Korean/English label for a raw defect category token. Unrecognized input
 * (there should be none -- see ``CATEGORY_LABEL_KEYS``) falls back to the
 * raw token itself rather than inventing a label for it.
 */
function describeCategory(t: Translate, category: string): string {
  const key = CATEGORY_LABEL_KEYS[category];
  return key ? t(key) : category;
}

function describeSingleReason(t: Translate, reason: string): string {
  if (flaggedSlideStage(reason) === "author") return t("reason.author");
  if (reason.startsWith(GATE_REASON_PREFIX)) {
    const categories = flaggedSlideCategories(reason);
    return categories.length > 0
      ? t("reason.gate", {
          categories: categories.map((category) => describeCategory(t, category)).join(", "),
        })
      : t("reason.gateUnknown");
  }
  return t("reason.other", { reason });
}

/**
 * Human-readable reason for the tooltip, in the ``flaggedSlides`` namespace.
 * Raw category tokens (``overflow``, ``off_canvas``, ...) are translated to a
 * localized label; the raw token itself stays available on
 * ``badge.categories`` for anything that needs it (logs, test ids), just
 * never shown to the end user in the Korean sentence.
 */
export function describeFlaggedSlideReason(t: Translate, badge: FlaggedSlideBadge): string {
  const reasons = badge.reasons.length > 0 ? badge.reasons : [badge.reason];
  return reasons.map((reason) => describeSingleReason(t, reason)).join(" ");
}
