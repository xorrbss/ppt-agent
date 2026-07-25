import { TEMPLATE_V2_SNAP_GRID } from "./template-v2-snapping.ts";

export type TemplateV2HistoryKeyboardIntent = "undo" | "redo";

export interface TemplateV2KeyboardEvent {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
  repeat?: boolean;
  target?: unknown;
  composedPath?(): unknown[];
}

export interface TemplateV2HistoryKeyboardAvailability {
  canUndo: boolean;
  canRedo: boolean;
  disabled?: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function editableTagName(value: Record<string, unknown>): string | null {
  const tagName =
    typeof value.tagName === "string"
      ? value.tagName
      : typeof value.nodeName === "string"
        ? value.nodeName
        : null;
  return tagName?.toLowerCase() ?? null;
}

/**
 * History shortcuts must stay out of native text-editing surfaces. Checking the
 * full composed path also covers text nodes and controls inside a shadow root.
 */
export function isTemplateV2EditableKeyboardTarget(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (value.isContentEditable === true) return true;
  if (
    value.contentEditable === "true" ||
    value.contentEditable === "plaintext-only"
  ) {
    return true;
  }
  const tagName = editableTagName(value);
  return tagName === "input" || tagName === "textarea" || tagName === "select";
}

function eventPath(event: TemplateV2KeyboardEvent): unknown[] {
  let composedPath: unknown[] = [];
  try {
    const result = event.composedPath?.();
    if (Array.isArray(result)) composedPath = result;
  } catch {
    // A synthetic event may expose a throwing composedPath implementation.
  }
  return event.target === undefined
    ? composedPath
    : [event.target, ...composedPath];
}

/**
 * Returns a history intent only when the chord can perform a real state change.
 *
 * - Ctrl/Cmd+Z is undo.
 * - Ctrl/Cmd+Shift+Z and Ctrl/Cmd+Y are redo variants.
 * - Repeated keydown events, Alt chords, editable targets, disabled history, and
 *   unavailable history directions are deliberate no-ops.
 */
export function getTemplateV2HistoryKeyboardIntent(
  event: TemplateV2KeyboardEvent,
  availability: TemplateV2HistoryKeyboardAvailability
): TemplateV2HistoryKeyboardIntent | null {
  if (
    availability.disabled ||
    event.repeat ||
    event.altKey ||
    (!event.ctrlKey && !event.metaKey) ||
    eventPath(event).some(isTemplateV2EditableKeyboardTarget)
  ) {
    return null;
  }

  const key = event.key.toLowerCase();
  const intent =
    key === "z"
      ? event.shiftKey
        ? "redo"
        : "undo"
      : key === "y" && !event.shiftKey
        ? "redo"
        : null;

  if (
    (intent === "undo" && !availability.canUndo) ||
    (intent === "redo" && !availability.canRedo)
  ) {
    return null;
  }
  return intent;
}

export type TemplateV2CanvasKeyboardIntent =
  | { type: "nudge"; deltaX: number; deltaY: number }
  | { type: "clear-selection" };

export interface TemplateV2CanvasKeyboardAvailability {
  hasSelection: boolean;
  canMove: boolean;
}

export const TEMPLATE_V2_NUDGE_STEP = 1;
export const TEMPLATE_V2_COARSE_NUDGE_STEP = TEMPLATE_V2_SNAP_GRID;

const NUDGE_AXES: Record<string, { x: number; y: number }> = {
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 },
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
};

/**
 * Keyboard equivalent of dragging on the canvas, so selection and movement stay
 * reachable without a pointer.
 *
 * - Arrow keys nudge by one unit; Shift+Arrow nudges by the snap grid step.
 * - Escape clears the selection.
 * - Auto-repeat is a deliberate no-op: every nudge commits its own history
 *   entry, so a held arrow key would flush the bounded undo stack.
 * - Ctrl/Cmd chords belong to history, and editable targets keep their own keys.
 */
export function getTemplateV2CanvasKeyboardIntent(
  event: TemplateV2KeyboardEvent,
  availability: TemplateV2CanvasKeyboardAvailability
): TemplateV2CanvasKeyboardIntent | null {
  if (
    event.repeat ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    eventPath(event).some(isTemplateV2EditableKeyboardTarget)
  ) {
    return null;
  }
  if (event.key === "Escape") {
    return availability.hasSelection ? { type: "clear-selection" } : null;
  }
  const axis = NUDGE_AXES[event.key];
  if (!axis || !availability.hasSelection || !availability.canMove) return null;
  const step = event.shiftKey
    ? TEMPLATE_V2_COARSE_NUDGE_STEP
    : TEMPLATE_V2_NUDGE_STEP;
  return {
    type: "nudge",
    deltaX: axis.x * step,
    deltaY: axis.y * step,
  };
}
