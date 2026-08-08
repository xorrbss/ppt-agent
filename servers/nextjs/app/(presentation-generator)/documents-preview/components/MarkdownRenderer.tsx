"use client";

import React, { useState, useEffect } from "react";

import { renderMarkdown } from "@/lib/safe-markdown";

interface MarkdownRendererProps {
  content: string;
}

const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content }) => {
  const [markdownContent, setMarkdownContent] = useState<string>("");

  useEffect(() => {
    const parseMarkdown = () => {
      try {
        setMarkdownContent(renderMarkdown(content));
      } catch (error) {
        console.error("Error parsing markdown:", error);
        setMarkdownContent("");
      }
    };

    parseMarkdown();
  }, [content]);

  return (
    <div
      className="prose prose-slate max-w-none mb-10"
      dangerouslySetInnerHTML={{ __html: markdownContent }}
    />
  );
};

export default MarkdownRenderer;
