"use client";

import React, { useRef, useEffect, useLayoutEffect, ReactNode } from "react";
import ReactDOM from "react-dom/client";
import { flushSync } from "react-dom";
import TiptapText from "./TiptapText";
import MarkdownInlineText from "./MarkdownInlineText";
import { getAdaptiveBlockText } from "@/lib/adaptiveBlockEdit";
import { collectMatchingPaths } from "@/lib/findDataPaths";

// Hangul (jamo + syllables), CJK ideographs, Kana. A 1-2 char Latin leaf is
// usually UI noise, but a 1-2 char CJK leaf is a real word (목표 / 성과 / 결론 /
// 개요), so it must stay editable in this Korean-first fork.
const CJK_TEXT_RE =
  /[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-鿿가-힣]/;

function isTrivialLeafText(text: string): boolean {
  const t = (text || "").trim();
  if (!t) return true;
  return t.length <= 2 && !CJK_TEXT_RE.test(t);
}

// How an editable text leaf is addressed back to slide content. Adaptive slides
// bind by the leaf's data-block-id (deterministic — survives duplicate text /
// reorder); legacy templates keep the original string-match path binding.
export type EditBinding =
  | { kind: "blockId"; key: string }
  | { kind: "path"; key: string };

function staticGetValueByPath(obj: any, path: string): any {
  if (!obj || !path) return undefined;
  const tokens = path.replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean);
  let current: any = obj;
  for (const token of tokens) {
    if (current == null) return undefined;
    current = current[token as keyof typeof current];
  }
  return current;
}

function staticReadBindingValue(data: any, binding: EditBinding): any {
  return binding.kind === "blockId"
    ? getAdaptiveBlockText(data, binding.key)
    : staticGetValueByPath(data, binding.key);
}

function staticDirectText(element: HTMLElement): string {
  let text = "";
  for (const node of Array.from(element.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) text += node.textContent || "";
  }
  return text;
}

function staticHasTextChildren(element: HTMLElement): boolean {
  return Array.from(element.children).some((child) =>
    staticDirectText(child as HTMLElement).trim().length > 1
  );
}

function staticShouldSkipElement(element: HTMLElement): boolean {
  if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(element.tagName)) return true;
  if (
    element.hasAttribute("role") ||
    element.hasAttribute("aria-label") ||
    element.hasAttribute("data-testid")
  ) return true;
  if (element.querySelector("img, svg, button, input, textarea, select, a[href]")) return true;
  const containerClasses = ["grid", "flex", "space-", "gap-", "container", "wrapper"];
  if (containerClasses.some((cls) => element.className.length > 0 && element.className.includes(cls))) {
    return true;
  }
  return isTrivialLeafText(staticDirectText(element).trim());
}

interface TiptapTextReplacerProps {
  children: ReactNode;
  slideData?: any;
  slideIndex?: number;
  readOnly?: boolean;
  // When true (adaptive group), graft the source leaf's data-block-id onto the
  // replacement container and bind edits by block id instead of string match.
  useBlockId?: boolean;
  onContentChange?: (
    content: string,
    binding: EditBinding,
    slideIndex?: number
  ) => void;
}

const NOOP_CONTENT_CHANGE = () => {};

const TiptapTextReplacer: React.FC<TiptapTextReplacerProps> = ({
  children,
  slideData,
  slideIndex,
  readOnly = false,
  useBlockId = false,
  onContentChange = NOOP_CONTENT_CHANGE,
}) => {

  

  const containerRef = useRef<HTMLDivElement>(null);
  // Track imperative DOM replacements without causing React to reconcile the
  // slide again while a PPTX capture is in progress.
  const processedElementsRef = useRef(new WeakSet<HTMLElement>());
  // Track created React roots to update content when slideData changes
  const rootsRef = useRef<
    Map<HTMLElement, { root: any; binding: EditBinding; fallbackText: string }>
  >(new Map());
  useLayoutEffect(() => {
    if (readOnly) {
      containerRef.current?.removeAttribute("data-markdown-rendered");
    }
  }, [slideData, slideIndex, readOnly]);

  useEffect(() => {
    if (!containerRef.current) return;

    const container = containerRef.current;

    const replaceTextElements = () => {
      // Get all elements in the container
      const allElements = container.querySelectorAll("*");

      // How many editable leaves with a given text we've already bound, so the Nth
      // duplicate on screen binds to the Nth matching content field (not always the
      // first). Reset per full walk.
      const pathOccurrence = new Map<string, number>();
      const readOnlyRenders: Array<() => void> = [];

      if (readOnly) {
        rootsRef.current.forEach(({ root, binding, fallbackText }) => {
          const newContent = binding.key
            ? staticReadBindingValue(slideData, binding) ?? fallbackText
            : fallbackText;
          readOnlyRenders.push(() =>
            root.render(<MarkdownInlineText content={newContent} />)
          );
        });
      }

      allElements.forEach((element) => {
        const htmlElement = element as HTMLElement;

        // Skip if already processed
       
        if (
          processedElementsRef.current.has(htmlElement) ||
          htmlElement.classList.contains("tiptap-text-editor") ||
          htmlElement.closest(".tiptap-text-editor") ||
          htmlElement.closest("[data-tiptap-replacer-root]") ||
          // Text migrated to the in-tree <EditableText> owns its own editing; the
          // DOM-surgery path must not also process it (would double-bind/leak).
          htmlElement.closest("[data-editable-native]")
        ) {
          return;
        }

        // console.log("htmlElement", htmlElement);
        // Skip if element is inside an ignored element tree
        if (isInIgnoredElementTree(htmlElement)) return;

        // Get direct text content (not from child elements)
        const directTextContent = staticDirectText(htmlElement);
        const trimmedText = directTextContent.trim();

        // Check if element has meaningful text content
        if (isTrivialLeafText(trimmedText)) return;
        
        // Skip elements that contain other elements with text (to avoid double processing)
        if (staticHasTextChildren(htmlElement)) return;
        
        // Skip certain element types that shouldn't be editable
        if (staticShouldSkipElement(htmlElement)) return;

        // Get all computed styles to preserve them
        const allClasses = Array.from(htmlElement.classList);
        const allStyles = htmlElement.getAttribute("style");

        // Adaptive binding: the editable leaf's id lives on itself or (for
        // bullets/column items) on the nearest [data-block-id] ancestor.
        const bid = useBlockId
          ? htmlElement.getAttribute("data-block-id") ||
            htmlElement.closest("[data-block-id]")?.getAttribute("data-block-id") ||
            null
          : null;
        // An explicit data-edit-path (on the leaf or nearest ancestor) is the
        // robust opt-in for custom templates: it binds by author-declared path
        // instead of matching rendered text. Preferred over the string-match
        // fallback below.
        const explicitPath = useBlockId
          ? null
          : htmlElement.getAttribute("data-edit-path") ||
            htmlElement.closest("[data-edit-path]")?.getAttribute("data-edit-path") ||
            null;
        let binding: EditBinding;
        if (bid) {
          binding = { kind: "blockId", key: bid };
        } else if (explicitPath) {
          binding = { kind: "path", key: explicitPath };
        } else {
          // Occurrence-based path binding: pick the Nth matching field for the Nth
          // leaf of this text, so duplicate text no longer all binds to the first.
          const matches = collectMatchingPaths(slideData, trimmedText);
          const occ = pathOccurrence.get(trimmedText) ?? 0;
          pathOccurrence.set(trimmedText, occ + 1);
          binding = { kind: "path", key: matches[occ] ?? matches[0] ?? "" };
        }

        // Create a container for the TiptapText
        const tiptapContainer = document.createElement("div");
        tiptapContainer.style.cssText = allStyles || "";
        tiptapContainer.className = Array.from(allClasses).join(" ");
        tiptapContainer.setAttribute("data-tiptap-replacer-root", "true");
        // Carry the block id onto the replacement so the editor can bind to it
        // (harmless in readOnly; the export converter ignores data-* attributes).
        if (bid) tiptapContainer.setAttribute("data-block-id", bid);

        // Replace the element
        if(htmlElement.parentNode) {
        htmlElement.parentNode.replaceChild(tiptapContainer, htmlElement);
        // Mark as processed
        htmlElement.innerHTML = "";
        }
        processedElementsRef.current.add(htmlElement);
        // Render TiptapText
        const root = ReactDOM.createRoot(tiptapContainer);
        const initialContent = binding.key
          ? staticReadBindingValue(slideData, binding) ?? trimmedText
          : trimmedText;
        rootsRef.current.set(tiptapContainer, {
          root,
          binding,
          fallbackText: trimmedText,
        });
        const editableText = (
          readOnly ? (
            <MarkdownInlineText content={initialContent} />
          ) : (
            <TiptapText
              content={initialContent}
              onContentChange={(content: string) => {
                if (binding.key && onContentChange) {
                  onContentChange(content, binding, slideIndex);
                }
              }}
              placeholder="텍스트를 입력하세요..."
            />
          )
        );
        if (readOnly) {
          readOnlyRenders.push(() => root.render(editableText));
        } else {
          root.render(editableText);
        }
      });

      if (readOnly && container) {
        // Export waits for data-markdown-rendered. Commit all nested roots in
        // one batch before publishing readiness so capture cannot observe an
        // empty or stale replacement container.
        flushSync(() => readOnlyRenders.forEach((render) => render()));
        container.setAttribute("data-markdown-rendered", "true");
      }
    };

  
    // Replace text elements after a short delay to ensure DOM is ready
    const timer = setTimeout(replaceTextElements, readOnly ? 250 : 1000);

    return () => {
      clearTimeout(timer);
    };
  }, [slideData, slideIndex, readOnly, useBlockId, onContentChange]);
  
  // When slideData changes, update existing editors' content using the stored binding
  useEffect(() => {
    if (readOnly) return;
    if (!rootsRef.current || rootsRef.current.size === 0) return;
    rootsRef.current.forEach(({ root, binding, fallbackText }) => {
      const newContent = binding.key
        ? staticReadBindingValue(slideData, binding) ?? fallbackText
        : fallbackText;
      root.render(
        readOnly ? (
          <MarkdownInlineText content={newContent} />
        ) : (
          <TiptapText
            content={newContent}
            onContentChange={(content: string) => {
              if (binding.key && onContentChange) {
                onContentChange(content, binding, slideIndex);
              }
            }}
            placeholder="텍스트를 입력하세요..."
          />
        )
      );
    });
  }, [slideData, slideIndex, readOnly, onContentChange]);
  // helper functions
    // Function to check if element is inside an ignored element tree
    const isInIgnoredElementTree = (element: HTMLElement): boolean => {
      // List of element types that should be ignored entirely with all their children
      const ignoredElementTypes = [
        "TABLE",
        "TBODY",
        "THEAD",
        "TFOOT",
        "TR",
        "TD",
        "TH", // Table elements
        "SVG",
        "G",
        "PATH",
        "CIRCLE",
        "RECT",
        "LINE", // SVG elements
        "CANVAS", // Canvas element
        "VIDEO",
        "AUDIO", // Media elements
        "IFRAME",
        "EMBED",
        "OBJECT", // Embedded content
        "SELECT",
        "OPTION",
        "OPTGROUP", // Select dropdown elements
        "SCRIPT",
        "STYLE",
        "NOSCRIPT", // Script/style elements
      ];

      // List of class patterns that indicate ignored element trees
      const ignoredClassPatterns = [
        "chart",
        "graph",
        "visualization", // Chart/graph components
        "menu",
        "dropdown",
        "tooltip", // UI components
        "editor",
        "wysiwyg", // Editor components
        "calendar",
        "datepicker", // Date picker components
        "slider",
        "carousel",
        "flowchart",
        "mermaid",
        "diagram",
      ];

      // Check if current element or any parent is in ignored list
      let currentElement: HTMLElement | null = element;
      while (currentElement) {
        // Check element type
        if (ignoredElementTypes.includes(currentElement.tagName)) {
          return true;
        }

        // Check class patterns
        const className =
          currentElement.className.length > 0
            ? currentElement.className.toLowerCase()
            : "";
        if (
          ignoredClassPatterns.some((pattern) => className.includes(pattern))
        ) {
          return true;
        }
        if (currentElement.id.includes("mermaid")) {
          return true;
        }

        // Check for specific attributes that indicate non-text content
        if (
          currentElement.hasAttribute("contenteditable") ||
          currentElement.hasAttribute("data-chart") ||
          currentElement.hasAttribute("data-visualization") ||
          currentElement.hasAttribute("data-interactive")
        ) {
          return true;
        }

        currentElement = currentElement.parentElement;
      }
      return false;
    };

    // Resolve nested values by path like "a.b[0].c"

    // Read the current value for a binding. Block-id resolution is the shared
    // util (lock-step with the updateAdaptiveBlock reducer); path is the legacy
    // string-match address.

    // Helper function to get only direct text content (not from children)

    // Helper function to check if element has child elements with text

    // Helper function to determine if element should be skipped
      // Skip form elements

      // Skip elements with certain roles or types

      // Skip elements that contain interactive content (simplified since we now use isInIgnoredElementTree)

      // Skip container elements (elements that primarily serve as layout containers)

      // Skip very short text that might be UI elements — but keep short CJK words
      // (목표 / 성과 / 개요) editable.

  return (
    <div ref={containerRef} className="tiptap-text-replacer">
      {children}
    </div>
  );
};

export default TiptapTextReplacer;
