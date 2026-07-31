"use client";

import React, { useEffect } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import { BubbleMenu } from "@tiptap/react/menus";
import StarterKit from "@tiptap/starter-kit";
import { Markdown, type MarkdownStorage } from "tiptap-markdown";
import {
  Bold,
  Italic,
  Underline as UnderlinedIcon,
  Strikethrough,
  Code,
} from "lucide-react";


interface TiptapTextProps {
  content: string;
 
  onContentChange?: (content: string) => void;
  className?: string;
  placeholder?: string;
 
}

const TiptapText: React.FC<TiptapTextProps> = ({
  content,
  onContentChange,
  className = "",
  placeholder = "텍스트를 입력하세요...",
}) => {
  const editor = useEditor({
    extensions: [StarterKit, Markdown],
    content: content || placeholder,

    editorProps: {
      attributes: {
        class: `outline-none focus:outline-none transition-all duration-200 ${className}`,
        "data-placeholder": placeholder,
      },
    },
    onBlur: ({ editor }) => {
      // const element = editor?.options.element;
      // element?.classList.add("tiptap-text-edited");
      const markdown = (
        editor.storage as unknown as { markdown: MarkdownStorage }
      ).markdown.getMarkdown();
      if (onContentChange) {
        onContentChange(markdown);
      }
    },
    editable: true,
    immediatelyRender: false,
    // Tiptap 3 disables transaction-driven React renders by default. Preserve
    // the v2 behavior because toolbar active states are derived during render.
    shouldRerenderOnTransaction: true,
  });

  // Update editor content when content prop changes
  useEffect(() => {
    if (!editor) return;
    // Compare against current plain text to avoid unnecessary updates
    const currentText = (
      editor.storage as unknown as { markdown: MarkdownStorage }
    ).markdown.getMarkdown();
    if ((content || "") !== currentText) {
      editor.commands.setContent(content || "", { emitUpdate: false });
    }
  }, [content, editor]);

 

  if (!editor) {
    return <div className={className}>{content || placeholder}</div>;
  }

  return (
    <>
      <BubbleMenu
        editor={editor}
        className="z-50"
      >
        <div
          style={{
            zIndex: 100,
          }}
          className="flex text-black bg-white  rounded-lg shadow-lg p-2 gap-1 border border-gray-200 z-50"
        >
          <button
            onClick={() => editor?.chain().focus().toggleBold().run()}
            className={`p-1 rounded hover:bg-gray-100 transition-colors ${
              editor?.isActive("bold") ? "bg-blue-100 text-blue-600" : ""
            }`}
            title="굵게"
          >
            <Bold className="h-4 w-4" />
          </button>
          <button
            onClick={() => editor?.chain().focus().toggleItalic().run()}
            className={`p-1 rounded hover:bg-gray-100 transition-colors ${
              editor?.isActive("italic") ? "bg-blue-100 text-blue-600" : ""
            }`}
            title="기울임"
          >
            <Italic className="h-4 w-4" />
          </button>
          <button
            onClick={() => editor?.chain().focus().toggleUnderline().run()}
            className={`p-1 rounded hover:bg-gray-100 transition-colors ${
              editor?.isActive("underline") ? "bg-blue-100 text-blue-600" : ""
            }`}
            title="밑줄"
          >
            <UnderlinedIcon className="h-4 w-4" />
          </button>
          <button
            onClick={() => editor?.chain().focus().toggleStrike().run()}
            className={`p-1 rounded hover:bg-gray-100 transition-colors ${
              editor?.isActive("strike") ? "bg-blue-100 text-blue-600" : ""
            }`}
            title="취소선"
          >
            <Strikethrough className="h-4 w-4" />
          </button>
          <button
            onClick={() => editor?.chain().focus().toggleCode().run()}
            className={`p-1 rounded hover:bg-gray-100 transition-colors ${
              editor?.isActive("code") ? "bg-blue-100 text-blue-600" : ""
            }`}
            title="코드"
          >
            <Code className="h-4 w-4" />
          </button>
        </div>
      </BubbleMenu>

      <EditorContent
        editor={editor}
        className={`tiptap-text-editor w-full`}
        style={{
          // Ensure the editor maintains the same visual appearance
          lineHeight: "inherit",
          fontSize: "inherit",
          fontWeight: "inherit",
          fontFamily: "inherit",
          color: "inherit",
          textAlign: "inherit",
        }}
      />
    </>
  );
};

export default TiptapText;
