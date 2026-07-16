import { useEffect, useCallback } from 'react';

type KeyboardEvent = {
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
  callback: (e: KeyboardEvent) => void,
  deps: any[] = []
) => {
  const handleKeyPress = useCallback(
    (event: KeyboardEvent) => {
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
    [callback, ...deps]
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyPress as any);
    return () => {
      document.removeEventListener('keydown', handleKeyPress as any);
    };
  }, [handleKeyPress]);
}; 