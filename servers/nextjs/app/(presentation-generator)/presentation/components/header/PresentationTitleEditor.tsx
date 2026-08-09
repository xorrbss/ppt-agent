"use client";
import { Check, Pencil, X } from "lucide-react";
import React from "react";

import MarkdownRenderer from "@/components/MarkDownRender";
import ToolTip from "@/components/ToolTip";
import { cn } from "@/lib/utils";
import type { PresentationData } from "@/store/slices/presentationGeneration";

const PresentationTitleEditor = ({
  isEditingTitle,
  draftTitle,
  setDraftTitle,
  titleInputRef,
  titleBlurIntentRef,
  handleTitleBlur,
  commitTitleEdit,
  cancelTitleEdit,
  beginTitleEdit,
  onTitleSaveMouseDown,
  onTitleCancelMouseDown,
  mutationsEnabled,
  isStreaming,
  presentationData,
}: {
  isEditingTitle: boolean;
  draftTitle: string;
  setDraftTitle: (value: string) => void;
  titleInputRef: React.RefObject<HTMLInputElement | null>;
  titleBlurIntentRef: React.RefObject<"none" | "save" | "cancel">;
  handleTitleBlur: () => void;
  commitTitleEdit: () => void;
  cancelTitleEdit: () => void;
  beginTitleEdit: () => void;
  onTitleSaveMouseDown: (e: React.MouseEvent) => void;
  onTitleCancelMouseDown: (e: React.MouseEvent) => void;
  mutationsEnabled: boolean;
  isStreaming: boolean | null;
  presentationData: PresentationData | null;
}) =>
  isEditingTitle ? (
    <div className="flex w-full max-w-[450px] items-stretch gap-0.5 rounded-[14px] border border-[#E4E2EB] bg-white pl-3.5 pr-1 py-1 shadow-[0_2px_12px_rgba(17,3,31,0.06)] ring-2 ring-[#5141e5]/15">
      <input
        ref={titleInputRef}
        value={draftTitle}
        onChange={(e) => setDraftTitle(e.target.value)}
        onBlur={handleTitleBlur}
        onKeyDown={(e) => {
          // Skip while an IME composition is active so confirming Korean input doesn't commit.
          if (e.key === "Enter" && !e.nativeEvent.isComposing) {
            e.preventDefault();
            titleBlurIntentRef.current = "save";
            commitTitleEdit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            titleBlurIntentRef.current = "cancel";
            cancelTitleEdit();
          }
        }}
        placeholder="발표자료 제목"
        className="min-w-0 flex-1 bg-transparent py-2 pr-2 font-unbounded text-base leading-tight text-[#101323] placeholder:text-[#101323]/35 outline-none border-0 focus:ring-0"
        aria-label="발표자료 제목"
      />
      <div className="flex shrink-0 items-center gap-0.5 border-l border-[#EDECEC] pl-1 ml-0.5">
        <ToolTip content="저장 · Enter">
          <button
            type="button"
            onMouseDown={onTitleSaveMouseDown}
            onClick={commitTitleEdit}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-[#5141e5] hover:bg-[#5141e5]/10 transition-colors"
            aria-label="제목 저장"
          >
            <Check className="h-4 w-4" strokeWidth={2.25} />
          </button>
        </ToolTip>
        <ToolTip content="취소 · Esc">
          <button
            type="button"
            onMouseDown={onTitleCancelMouseDown}
            onClick={cancelTitleEdit}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-[#101323]/55 hover:bg-[#F6F6F9] hover:text-[#101323] transition-colors"
            aria-label="제목 편집 취소"
          >
            <X className="h-4 w-4" strokeWidth={2.25} />
          </button>
        </ToolTip>
      </div>
    </div>
  ) : (
    <button
      type="button"
      onClick={beginTitleEdit}
      disabled={!mutationsEnabled || isStreaming || !presentationData}
      data-testid="presentation-title-edit"
      className={cn(
        "group/title flex w-full min-w-0 items-center gap-2.5 rounded-[14px] px-3 py-2 text-left -mx-3 transition-colors",
        "hover:bg-[#F6F6F9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141e5] focus-visible:ring-offset-2",
        "disabled:pointer-events-none disabled:opacity-100 disabled:hover:bg-transparent"
      )}
    >
      <h2 className="min-w-0 flex-1 font-unbounded text-lg leading-snug text-[#101323]">
        <MarkdownRenderer
          content={presentationData?.title || "발표자료"}
          className="mb-0 min-w-0 overflow-hidden text-ellipsis line-clamp-1 text-sm text-[#101323] prose-p:my-0 prose-headings:my-0"
        />
      </h2>
      {mutationsEnabled && presentationData && !isStreaming && (
        <Pencil
          className="h-3.5 w-3.5 shrink-0 text-[#101323]/40 transition-all duration-200 group-hover/title:text-[#5141e5] opacity-80 sm:opacity-0 sm:group-hover/title:opacity-100 group-hover/title:opacity-100"
          aria-hidden
        />
      )}
    </button>
  );

export default PresentationTitleEditor;
