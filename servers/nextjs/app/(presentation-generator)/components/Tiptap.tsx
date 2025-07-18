"use client";

import { useEditor, EditorContent, BubbleMenu } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "tiptap-markdown";
import Underline from "@tiptap/extension-underline";
import {
  Bold,
  Italic,
  Underline as UnderlinedIcon,
  Strikethrough,
  Code,
} from "lucide-react";



const TipTapEditor = ({
  content,
}: {
  content: string;

}) => {

  const editor = useEditor({
    extensions: [StarterKit, Markdown, Underline],
    content: content,
    editorProps: {
      attributes: {
        class: "outline-none  transition-all duration-200",
      },
    },
    immediatelyRender: false,
  });

  return (
    <div className="relative">
      <BubbleMenu editor={editor} tippyOptions={{ duration: 100 }}>
        <div className="flex bg-white rounded-lg shadow-lg p-2 gap-1 border-r pr-2">
          <button
            onClick={() => editor?.chain().focus().toggleBold().run()}
            className={`p-1 rounded hover:bg-gray-100  ${editor?.isActive("bold") ? "bg-gray-200" : ""
              }`}
          >
            <Bold className="h-4 w-4" />
          </button>
          <button
            onClick={() => editor?.chain().focus().toggleItalic().run()}
            className={`p-1 rounded hover:bg-gray-100 ${editor?.isActive("italic") ? "bg-gray-200" : ""
              }`}
          >
            <Italic className="h-4 w-4" />
          </button>
          <button
            onClick={() => editor?.chain().focus().toggleUnderline().run()}
            className={`p-1 rounded hover:bg-gray-100 ${editor?.isActive("underline") ? "bg-gray-200" : ""
              }`}
          >
            <UnderlinedIcon className="h-4 w-4" />
          </button>
          <button
            onClick={() => editor?.chain().focus().toggleStrike().run()}
            className={`p-1 rounded hover:bg-gray-100 ${editor?.isActive("strike") ? "bg-gray-200" : ""
              }`}
          >
            <Strikethrough className="h-4 w-4" />
          </button>
          <button
            onClick={() => editor?.chain().focus().toggleCode().run()}
            className={`p-1 rounded hover:bg-gray-100 ${editor?.isActive("codeBlock") ? "bg-gray-200" : ""
              }`}
          >
            <Code className="h-4 w-4" />
          </button>
        </div>
      </BubbleMenu>

      <EditorContent
        className={`min-w-[100px] w-full max-md:pointer-events-none ${editor?.getText() ? "" : `hover:outline hover:outline-gray-400`
          } `}
        onBlur={() => {
          const markdown = editor?.storage.markdown.getMarkdown();
          console.log("🔍 markdown", markdown);
        }}

        editor={editor}
      />
    </div>
  );
};

export default TipTapEditor;
