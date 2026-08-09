export type EditorShortcutKey =
  | "Mod"
  | "Shift"
  | "Alt"
  | "Click"
  | "Backspace"
  | "Delete"
  | "C"
  | "G"
  | "J"
  | "K"
  | "V"
  | "Y"
  | "Z"
  | "?";

export type EditorShortcut = {
  id:
    | "multi-select"
    | "group"
    | "delete"
    | "copy"
    | "paste"
    | "undo"
    | "redo"
    | "bring-forward"
    | "bring-to-front"
    | "send-backward"
    | "send-to-back"
    | "shortcut-help";
  chords: EditorShortcutKey[][];
};

export type EditorShortcutSection = {
  id: "selection" | "editing" | "arrange" | "help";
  shortcuts: EditorShortcut[];
};

export const EDITOR_SHORTCUT_SECTIONS: EditorShortcutSection[] = [
  {
    id: "selection",
    shortcuts: [
      {
        id: "multi-select",
        chords: [["Shift", "Click"]],
      },
      {
        id: "group",
        chords: [["Mod", "G"]],
      },
      {
        id: "delete",
        chords: [["Backspace"], ["Delete"]],
      },
    ],
  },
  {
    id: "editing",
    shortcuts: [
      {
        id: "undo",
        chords: [["Mod", "Z"]],
      },
      {
        id: "redo",
        chords: [["Mod", "Shift", "Z"], ["Mod", "Y"]],
      },
      {
        id: "copy",
        chords: [["Mod", "C"]],
      },
      {
        id: "paste",
        chords: [["Mod", "V"]],
      },
    ],
  },
  {
    id: "arrange",
    shortcuts: [
      {
        id: "bring-forward",
        chords: [["Alt", "K"]],
      },
      {
        id: "bring-to-front",
        chords: [["Shift", "Alt", "K"]],
      },
      {
        id: "send-backward",
        chords: [["Alt", "J"]],
      },
      {
        id: "send-to-back",
        chords: [["Shift", "Alt", "J"]],
      },
    ],
  },
  {
    id: "help",
    shortcuts: [
      {
        id: "shortcut-help",
        chords: [["?"]],
      },
    ],
  },
];

export function editorShortcutById(id: EditorShortcut["id"]) {
  return EDITOR_SHORTCUT_SECTIONS.flatMap(
    (section) => section.shortcuts,
  ).find((shortcut) => shortcut.id === id);
}

export function shortcutKeyLabel(
  key: EditorShortcutKey,
  applePlatform: boolean,
) {
  if (!applePlatform) {
    if (key === "Mod") return "Ctrl";
    return key;
  }

  switch (key) {
    case "Mod":
      return "⌘";
    case "Shift":
      return "⇧";
    case "Alt":
      return "⌥";
    case "Backspace":
      return "⌫";
    default:
      return key;
  }
}
