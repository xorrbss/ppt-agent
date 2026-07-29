"use client";
import React from "react";

import MarkdownInlineText from "./MarkdownInlineText";
import TiptapText from "./TiptapText";
import { useEditableText } from "./EditableTextContext";

interface EditableTextProps {
  /** Path into the slide content to write on edit (e.g. "title", "items[0].heading"). */
  path: string;
  /** The resolved display value (the layout keeps its own default fallback). */
  value?: string | null;
  /** Semantic wrapper tag — keep it the same tag the layout used (h1/p/span/li…). */
  as?: keyof React.JSX.IntrinsicElements;
  className?: string;
  style?: React.CSSProperties;
  placeholder?: string;
}

/**
 * In-tree editable text — the replacement for the DOM-surgery TiptapTextReplacer.
 *
 * Read-only / export: renders the semantic tag with SYNCHRONOUS inline markdown,
 * so the exported/screenshotted DOM has the same semantic leaves and first-paint
 * emphasis the export runtime expects. Edit: renders an inline Tiptap editor that
 * writes back through the context by explicit `path` (no fragile text matching).
 *
 * The `data-editable-native` marker tells the legacy TiptapTextReplacer to leave
 * this subtree alone, so migrated and not-yet-migrated templates coexist while the
 * rollout is in progress.
 */
// Phrasing/heading tags whose content model is inline-only, so wrapping the
// Tiptap editor's block <div> in them produces invalid HTML (<p><div></div></p>)
// and a hydration error. In edit mode we swap these for a <div> — edit mode is
// the in-app editor (never exported), and className/style carry the visuals.
const INLINE_ONLY_TAGS = new Set([
  "p", "span", "a", "em", "strong", "b", "i", "u", "s", "small", "mark",
  "code", "sub", "sup", "label", "h1", "h2", "h3", "h4", "h5", "h6",
]);

const EditableText: React.FC<EditableTextProps> = ({
  path,
  value,
  as = "div",
  className,
  style,
  placeholder,
}) => {
  const { isEditMode, onEdit } = useEditableText();
  const Tag = as as React.ElementType;
  const text = value == null ? "" : String(value);

  if (!isEditMode) {
    return (
      <Tag data-editable-native className={className} style={style}>
        <MarkdownInlineText content={text} />
      </Tag>
    );
  }

  const EditTag: React.ElementType = INLINE_ONLY_TAGS.has(String(as).toLowerCase())
    ? "div"
    : Tag;

  return (
    <EditTag data-editable-native className={className} style={style}>
      <TiptapText
        content={text}
        placeholder={placeholder}
        onContentChange={(next) => onEdit(path, next)}
      />
    </EditTag>
  );
};

export default EditableText;
