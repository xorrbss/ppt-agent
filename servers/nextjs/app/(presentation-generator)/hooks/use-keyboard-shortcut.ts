import { useEffect, useCallback } from 'react';

/** Structural subset of the DOM KeyboardEvent this hook needs. */
type ShortcutKeyboardEvent = {
  key: string;
  ctrlKey: boolean;
  shiftKey: boolean;
  target?: EventTarget | null;
  preventDefault: () => void;
};

// This hook only drives slide-level undo/redo (Ctrl+Z / Ctrl+Y). When focus is in
// a text field / rich-text editor, hijacking the shortcut kills the field's native
// undo AND fires a second, slide-level undo that silently reverts another slide's
// committed edit — so let editable targets handle Ctrl+Z/Y themselves.
function isEditableTarget(target: EventTarget | null | undefined): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.closest !== 'function') return false;
  const tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true;
  if (el.isContentEditable) return true;
  return !!el.closest(
    '[contenteditable="true"], .ProseMirror, .tiptap-text-editor'
  );
}

export const useKeyboardShortcut = (
  keys: string[],
  callback: (e: ShortcutKeyboardEvent) => void
) => {
  const handleKeyPress = useCallback(
    (event: ShortcutKeyboardEvent) => {
      const isCtrlPressed = event.ctrlKey;

      if (
        keys.includes(event.key.toLowerCase()) &&
        isCtrlPressed &&
        !isEditableTarget(event.target)
      ) {
        event.preventDefault();
        callback(event);
      }
    },
    [callback, keys]
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyPress);
    return () => {
      document.removeEventListener('keydown', handleKeyPress);
    };
  }, [handleKeyPress]);
};
