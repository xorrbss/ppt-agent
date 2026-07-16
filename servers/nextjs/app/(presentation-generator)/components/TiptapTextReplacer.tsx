"use client";

import React, { useRef, useEffect, useState, ReactNode } from "react";
import ReactDOM from "react-dom/client";
import TiptapText from "./TiptapText";
import MarkdownInlineText from "./MarkdownInlineText";
import { useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "tiptap-markdown";
import Underline from "@tiptap/extension-underline";
import { getAdaptiveBlockText } from "@/lib/adaptiveBlockEdit";

const extensions = [StarterKit, Markdown, Underline];

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

const TiptapTextReplacer: React.FC<TiptapTextReplacerProps> = ({
  children,
  slideData,
  slideIndex,
  readOnly = false,
  useBlockId = false,
  onContentChange = () => {},
}) => {

  

  const containerRef = useRef<HTMLDivElement>(null);
  const [processedElements, setProcessedElements] = useState(
    new Set<HTMLElement>()
  );
  // Track created React roots to update content when slideData changes
  const rootsRef = useRef<
    Map<HTMLElement, { root: any; binding: EditBinding; fallbackText: string }>
  >(new Map());
  useEffect(() => {
    if (!containerRef.current) return;

    const container = containerRef.current;

    const replaceTextElements = () => {
      // Get all elements in the container
      const allElements = container.querySelectorAll("*");

      allElements.forEach((element) => {
        const htmlElement = element as HTMLElement;

        // Skip if already processed
       
        if (
          processedElements.has(htmlElement) ||
          htmlElement.classList.contains("tiptap-text-editor") ||
          htmlElement.closest(".tiptap-text-editor")
        ) {
          return;
        }

        // console.log("htmlElement", htmlElement);
        // Skip if element is inside an ignored element tree
        if (isInIgnoredElementTree(htmlElement)) return;

        // Get direct text content (not from child elements)
        const directTextContent = getDirectTextContent(htmlElement);
        const trimmedText = directTextContent.trim();

        // Check if element has meaningful text content
        if (isTrivialLeafText(trimmedText)) return;
        
        // Skip elements that contain other elements with text (to avoid double processing)
        if (hasTextChildren(htmlElement)) return;
        
        // Skip certain element types that shouldn't be editable
        if (shouldSkipElement(htmlElement)) return;

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
        const binding: EditBinding = bid
          ? { kind: "blockId", key: bid }
          : { kind: "path", key: findDataPath(slideData, trimmedText).path };

        // Create a container for the TiptapText
        const tiptapContainer = document.createElement("div");
        tiptapContainer.style.cssText = allStyles || "";
        tiptapContainer.className = Array.from(allClasses).join(" ");
        // Carry the block id onto the replacement so the editor can bind to it
        // (harmless in readOnly; the export converter ignores data-* attributes).
        if (bid) tiptapContainer.setAttribute("data-block-id", bid);

        // Replace the element
        if(htmlElement.parentNode) {
        htmlElement.parentNode.replaceChild(tiptapContainer, htmlElement);
        // Mark as processed
        htmlElement.innerHTML = "";
        }
        setProcessedElements((prev) => new Set(prev).add(htmlElement));
        // Render TiptapText
        const root = ReactDOM.createRoot(tiptapContainer);
        const initialContent = binding.key
          ? readBindingValue(slideData, binding) ?? trimmedText
          : trimmedText;
        rootsRef.current.set(tiptapContainer, {
          root,
          binding,
          fallbackText: trimmedText,
        });
        root.render(
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
      });

      if (readOnly && container) {
        container.setAttribute("data-markdown-rendered", "true");
      }
    };

  
    // Replace text elements after a short delay to ensure DOM is ready
    const timer = setTimeout(replaceTextElements, readOnly ? 250 : 1000);

    return () => {
      clearTimeout(timer);
    };
  }, [slideData, slideIndex, readOnly]);
  
  // When slideData changes, update existing editors' content using the stored binding
  useEffect(() => {
    if (!rootsRef.current || rootsRef.current.size === 0) return;
    rootsRef.current.forEach(({ root, binding, fallbackText }) => {
      const newContent = binding.key
        ? readBindingValue(slideData, binding) ?? fallbackText
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
  }, [slideData, slideIndex, readOnly]);
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
    const getValueByPath = (obj: any, path: string): any => {
      if (!obj || !path) return undefined;
      const tokens = path
        .replace(/\[(\d+)\]/g, ".$1")
        .split(".")
        .filter(Boolean);
      let current: any = obj;
      for (const token of tokens) {
        if (current == null) return undefined;
        current = current[token as keyof typeof current];
      }
      return current;
    };

    // Read the current value for a binding. Block-id resolution is the shared
    // util (lock-step with the updateAdaptiveBlock reducer); path is the legacy
    // string-match address.
    const readBindingValue = (data: any, binding: EditBinding): any =>
      binding.kind === "blockId"
        ? getAdaptiveBlockText(data, binding.key)
        : getValueByPath(data, binding.key);

    // Helper function to get only direct text content (not from children)
    const getDirectTextContent = (element: HTMLElement): string => {
      let text = "";
      const childNodes = Array.from(element.childNodes);
      for (const node of childNodes) {
        if (node.nodeType === Node.TEXT_NODE) {
          text += node.textContent || "";
        }
      }
      return text;
    };

    // Helper function to check if element has child elements with text
    const hasTextChildren = (element: HTMLElement): boolean => {
      const children = Array.from(element.children) as HTMLElement[];
      return children.some((child) => {
        const childText = getDirectTextContent(child).trim();
        return childText.length > 1;
      });
    };

    // Helper function to determine if element should be skipped
    const shouldSkipElement = (element: HTMLElement): boolean => {
      // Skip form elements
      if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(element.tagName)) {
        return true;
      }

      // Skip elements with certain roles or types
      if (
        element.hasAttribute("role") ||
        element.hasAttribute("aria-label") ||
        element.hasAttribute("data-testid")
      ) {
        return true;
      }

      // Skip elements that contain interactive content (simplified since we now use isInIgnoredElementTree)
      if (
        element.querySelector(
          "img, svg, button, input, textarea, select, a[href]"
        )
      ) {
        return true;
      }

      // Skip container elements (elements that primarily serve as layout containers)
      const containerClasses = [
        "grid",
        "flex",
        "space-",
        "gap-",
        "container",
        "wrapper",
      ];
      const hasContainerClass = containerClasses.some((cls) =>
        element.className.length > 0 ? element.className.includes(cls) : false
      );
      if (hasContainerClass) return true;

      // Skip very short text that might be UI elements — but keep short CJK words
      // (목표 / 성과 / 개요) editable.
      const text = getDirectTextContent(element).trim();
      if (isTrivialLeafText(text)) return true;

      return false;
    };

    // Helper function to find data path for text content
    const findDataPath = (
      data: any,
      targetText: string,
      path = ""
    ): {
      path: string;
      originalText: string;
    } => {
      if (!data || typeof data !== "object")
        return { path: "", originalText: "" };

      for (const [key, value] of Object.entries(data)) {
        const currentPath = path ? `${path}.${key}` : key;

        if (typeof value === "string" && value.trim() === targetText.trim()) {
          return { path: currentPath, originalText: value };
        }

        if (Array.isArray(value)) {
          for (let i = 0; i < value.length; i++) {
            const result = findDataPath(
              value[i],
              targetText,
              `${currentPath}[${i}]`
            );
            if (result.path) return result;
          }
        } else if (typeof value === "object" && value !== null) {
          const result = findDataPath(value, targetText, currentPath);
          if (result.path) return result;
        }
      }
      return { path: "", originalText: "" };
    };


  return (
    <div ref={containerRef} className="tiptap-text-replacer">
      {children}
    </div>
  );
};

export default TiptapTextReplacer;
