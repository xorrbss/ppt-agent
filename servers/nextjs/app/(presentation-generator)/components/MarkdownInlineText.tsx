"use client";

import React, { useMemo } from "react";
import { marked } from "marked";

interface MarkdownInlineTextProps {
  content: string;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * Renders inline markdown (e.g. **bold**) without block wrappers like <p>.
 * Used for export/preview where Tiptap edit mode is off.
 *
 * Parsing is SYNCHRONOUS (marked.parseInline returns a string when no async
 * extensions are configured) and memoised, so the emphasised HTML is present on
 * the very first paint. The previous async-useEffect version painted the raw
 * text (with literal **) first and only swapped in the bold on a later tick — a
 * headless export/screenshot capture could grab that first frame and bake `**`
 * into the exported deck.
 */
const MarkdownInlineText: React.FC<MarkdownInlineTextProps> = ({
  content,
  className = "",
  style,
}) => {
  const html = useMemo(() => {
    try {
      const parsed = marked.parseInline(content || "");
      // parseInline is sync here; guard in case an async config makes it a Promise.
      const base = typeof parsed === "string" ? parsed : content || "";
      // Mop up bold that CommonMark's flanking rules leave unparsed — e.g. a value
      // ending in punctuation glued to a following letter (**99.9%**를, common in
      // Korean number+particle copy). marked has already HTML-escaped the content,
      // so the surviving ** wrap safe text and this substitution can't inject HTML.
      return base.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
    } catch {
      return content || "";
    }
  }, [content]);

  return (
    <span
      className={className}
      style={style}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
};

export default MarkdownInlineText;
