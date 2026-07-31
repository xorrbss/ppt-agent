import DOMPurify from "isomorphic-dompurify";
import { marked } from "marked";

const MARKDOWN_SANITIZE_OPTIONS = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: ["base", "embed", "iframe", "link", "meta", "object", "script", "style"],
};

/**
 * Sanitizes HTML produced from untrusted Markdown before it reaches a
 * dangerouslySetInnerHTML render path.
 */
export function sanitizeMarkdownHtml(html: string): string {
  return DOMPurify.sanitize(html, MARKDOWN_SANITIZE_OPTIONS);
}

export function renderMarkdown(content: string): string {
  try {
    const parsed = marked.parse(content || "");
    return sanitizeMarkdownHtml(typeof parsed === "string" ? parsed : content || "");
  } catch {
    return "";
  }
}

export function renderInlineMarkdown(content: string): string {
  try {
    const parsed = marked.parseInline(content || "");
    return sanitizeMarkdownHtml(typeof parsed === "string" ? parsed : content || "");
  } catch {
    return sanitizeMarkdownHtml(content || "");
  }
}
