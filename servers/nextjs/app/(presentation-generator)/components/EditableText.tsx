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

  return (
    <Tag data-editable-native className={className} style={style}>
      <TiptapText
        content={text}
        placeholder={placeholder}
        onContentChange={(next) => onEdit(path, next)}
      />
    </Tag>
  );
};

export default EditableText;
