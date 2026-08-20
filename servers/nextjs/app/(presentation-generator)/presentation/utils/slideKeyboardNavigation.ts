export type SlideNavigationIntent = -1 | 1;

export interface SlideNavigationKeyboardEvent {
  key: string;
  altKey?: boolean;
  ctrlKey?: boolean;
  defaultPrevented?: boolean;
  isComposing?: boolean;
  metaKey?: boolean;
  repeat?: boolean;
  shiftKey?: boolean;
  target?: unknown;
  composedPath?(): unknown[];
}

type RecordLike = Record<string, unknown>;

function isRecord(value: unknown): value is RecordLike {
  return typeof value === "object" && value !== null;
}

function editableTagName(value: RecordLike): string | null {
  const tagName =
    typeof value.tagName === "string"
      ? value.tagName
      : typeof value.nodeName === "string"
        ? value.nodeName
        : null;
  return tagName?.toLowerCase() ?? null;
}

/**
 * Keep global editor shortcuts out of native and rich-text editing surfaces.
 * The structural checks deliberately work for both browser events and the
 * focused Node tests used by this frontend.
 */
export function isEditorEditableTarget(value: unknown): boolean {
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

function eventPath(event: SlideNavigationKeyboardEvent): unknown[] {
  let composedPath: unknown[] = [];
  try {
    const result = event.composedPath?.();
    if (Array.isArray(result)) composedPath = result;
  } catch {
    // Some synthetic events expose an unusable composedPath implementation.
  }
  return event.target === undefined ? composedPath : [event.target, ...composedPath];
}

/**
 * Resolves unmodified arrow keys to a slide-relative movement. It deliberately
 * respects a canvas/editor handler that already consumed the event, IME
 * composition, key repeat, modifiers, and editable targets.
 */
export function getSlideNavigationIntent(
  event: SlideNavigationKeyboardEvent,
): SlideNavigationIntent | null {
  if (
    event.defaultPrevented ||
    event.isComposing ||
    event.repeat ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey ||
    eventPath(event).some(isEditorEditableTarget)
  ) {
    return null;
  }

  if (event.key === "ArrowLeft" || event.key === "ArrowUp") return -1;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") return 1;
  return null;
}
