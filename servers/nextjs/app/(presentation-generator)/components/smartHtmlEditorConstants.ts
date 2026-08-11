export type SelectionRect = { left: number; top: number; width: number; height: number };

export const EXCLUDED_TEXT_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "IFRAME", "OBJECT", "EMBED", "SVG", "IMG", "VIDEO", "AUDIO", "CANVAS"]);
export const INLINE_TEXT_TAGS = new Set(["B", "STRONG", "I", "EM", "SPAN", "U", "A", "SMALL", "SUB", "SUP", "S", "MARK", "CODE", "KBD", "VAR", "ABBR", "CITE", "Q", "TIME", "BR", "WBR"]);

export const SMART_EDITOR_SELECTION_STYLE = `
  .smart-html-editor [contenteditable="true"] { cursor: text; user-select: text; -webkit-user-select: text; }
  .smart-html-editor [contenteditable="true"]:focus { outline: none; }
  .smart-html-editor [data-smart-editable-media="true"] { cursor: pointer; transition: opacity 160ms ease; }
  .smart-html-editor [data-smart-editable-media="true"]:hover { opacity: 0.86; }
  .smart-html-editor[data-smart-selecting="true"], .smart-html-editor[data-smart-selecting="true"] * { cursor: crosshair !important; }
`;
