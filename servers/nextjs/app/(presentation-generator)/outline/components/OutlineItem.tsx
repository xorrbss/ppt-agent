import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Grip } from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { renderMarkdown } from "@/lib/safe-markdown";

interface OutlineItemProps {
  slideOutline: {
    content: string;
  };
  id: string;
  index: number;
  isStreaming: boolean;
  isActiveStreaming?: boolean;
  isStableStreaming?: boolean;
  onUpdate?: (newContent: string) => void;
}

const outlineMarkdownClassName =
  "prose prose-sm max-w-none flex-1 font-syne text-base font-normal leading-normal text-[#666666] [overflow-wrap:anywhere] [&>*]:!my-0 [&>*+*]:!mt-[10px] [&_h1]:text-xl [&_h1]:font-medium [&_h1]:leading-normal [&_h1]:text-[#191919] [&_h2]:text-xl [&_h2]:font-medium [&_h2]:leading-normal [&_h2]:text-[#191919] [&_h3]:text-base [&_h3]:font-semibold [&_h3]:leading-normal [&_h3]:text-[#191919] [&_p]:text-base [&_p]:font-normal [&_p]:leading-normal [&_p]:text-[#666666] [&_strong]:font-semibold [&_strong]:text-[#191919] [&_ul]:!my-0 [&_ul]:list-none [&_ul]:space-y-1.5 [&_ul]:pl-0 [&_ul_li]:my-0 [&_ul_li]:bg-[url('/figma/outline-check.svg')] [&_ul_li]:bg-[length:20px_20px] [&_ul_li]:bg-[position:left_2px] [&_ul_li]:bg-no-repeat [&_ul_li]:pl-7 [&_ul_li]:text-base [&_ul_li]:font-normal [&_ul_li]:leading-6 [&_ul_li]:text-[#333333]";

export function OutlineItem({
  id,
  index,
  slideOutline,
  isStreaming,
  isActiveStreaming = false,
  isStableStreaming = false,
  onUpdate,
}: OutlineItemProps) {
  useEffect(() => {
    if (isStreaming) {
      const outlineItem = document.getElementById(`outline-item-${index}`);
      if (outlineItem) {
        outlineItem.scrollIntoView({
          behavior: "smooth",
          block: "center",
          inline: "nearest",
        });
      }
    }
  }, [index, isStreaming, outlines.length, slideOutline]);

  const handleSlideChange = (newOutline: any) => {
    if (isStreaming) return;
    const newData = outlines?.map((each: any, idx: any) => {
      if (idx === index - 1) {
        return {
          content: newOutline,
        };
      }
      return each;
    });

    if (!newData) return;
    dispatch(setOutlines(newData));
  };

  // DnD sortable configuration
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id, disabled: isStreaming });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };
  const handleSlideDelete = () => {
    if (isStreaming) return;
    dispatch(deleteSlideOutline({ index: index - 1 }));
  };

  const editorRef = useRef<HTMLTextAreaElement>(null);
  const throttleRef = useRef<number | null>(null);
  const [markdownDraft, setMarkdownDraft] = useState(
    slideOutline.content || ""
  );
  const [isEditingMarkdown, setIsEditingMarkdown] = useState(false);
  const [renderedHtml, setRenderedHtml] = useState<string>("");

  useEffect(() => {
    setMarkdownDraft(slideOutline.content || "");
  }, [slideOutline.content]);

  useEffect(() => {
    if (!isEditingMarkdown) return;
    const editor = editorRef.current;
    if (!editor) return;

    editor.focus();
    const end = editor.value.length;
    editor.setSelectionRange(end, end);
  }, [isEditingMarkdown]);

  const handleMarkdownBlur = () => {
    if (markdownDraft !== slideOutline.content) {
      onUpdate?.(markdownDraft);
    }
    setIsEditingMarkdown(false);
  };

  const handleStartMarkdownEdit = () => {
    setMarkdownDraft(slideOutline.content || "");
    setIsEditingMarkdown(true);
  };

  const handleMarkdownKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Tab") return;

    event.preventDefault();
    const target = event.currentTarget;
    const start = target.selectionStart;
    const end = target.selectionEnd;
    const updatedValue = `${markdownDraft.slice(
      0,
      start
    )}\t${markdownDraft.slice(end)}`;

    setMarkdownDraft(updatedValue);
    requestAnimationFrame(() => {
      target.selectionStart = target.selectionEnd = start + 1;
    });
  };

  useEffect(() => {
    if (!isStreaming || !isActiveStreaming) return;
    const content = slideOutline.content || "";

    if (throttleRef.current) {
      window.clearTimeout(throttleRef.current);
    }
    throttleRef.current = window.setTimeout(() => {
      try {
        setRenderedHtml(renderMarkdown(content));
      } catch {
        setRenderedHtml("");
      }
    }, 60);

    return () => {
      if (throttleRef.current) {
        window.clearTimeout(throttleRef.current);
      }
    };
  }, [isStreaming, isActiveStreaming, slideOutline.content]);

  const stableHtml = useMemo(() => {
    if (!isStreaming || isActiveStreaming) return null;
    if (!isStableStreaming) return null;
    try {
      return renderMarkdown(slideOutline.content || "");
    } catch {
      return null;
    }
  }, [isStreaming, isActiveStreaming, isStableStreaming, slideOutline.content]);

  const previewHtml = useMemo(() => {
    if (isStreaming) return "";
    try {
      return renderMarkdown(slideOutline.content || "");
    } catch {
      return "";
    }
  }, [isStreaming, slideOutline.content]);

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`mb-4 bg-white rounded-[12px] group border p-10 relative font-syne  transition-all duration-500 hover:shadow-[0_10px_24px_0_rgba(15,23,42,0.12)] ${
        isEditing
          ? "border-[#BDB4FE] shadow-[0_6.6px_13.2px_0_rgba(0,0,0,0.10)]"
          : "border-transparent shadow-sm"
      } ${isDragging ? "opacity-50" : ""}`}
    >
      <div className="flex items-start gap-3 rounded-[8px]">
        <div
          {...attributes}
          {...listeners}
          className=" flex items-center justify-center relative cursor-grab"
        >
          <Grip aria-hidden="true" className="h-6 w-6 text-[#191919]" />
        </div>

        <div
          id={`outline-item-${index}`}
          className="flex flex-col basis-full gap-2"
        >
          <p className="text-black w-fit text-[10px] font-medium  bg-white border border-[#EDEEEF] rounded-[80px] px-2.5">
            슬라이드 {index}
          </p>

          {isStreaming ? (
            isActiveStreaming ? (
              <div
                className="text-sm flex-1 font-normal prose prose-sm max-w-none"
                dangerouslySetInnerHTML={{ __html: renderedHtml || "" }}
              />
            ) : stableHtml ? (
              <div
                className="text-sm flex-1 font-normal prose prose-sm max-w-none"
                dangerouslySetInnerHTML={{ __html: stableHtml }}
              />
            ) : (
              <p className="text-sm  flex-1 font-normal">
                {slideOutline.content || ""}
              </p>
            )
          ) : isEditingMarkdown ? (
            <Textarea
              ref={editorRef}
              value={markdownDraft}
              onChange={(event) => setMarkdownDraft(event.target.value)}
              onBlur={handleMarkdownBlur}
              onKeyDown={handleMarkdownKeyDown}
              spellCheck={false}
              placeholder="Enter markdown content here..."
              className="min-h-[140px] resize-y rounded-[8px] border-[#D8D8DF] bg-[#FBFBFC] px-3 py-3 font-mono text-[13px] leading-6 text-[#191919] shadow-none focus-visible:border-[#7A5AF8] focus-visible:ring-2 focus-visible:ring-[#7A5AF8]/20"
            />
          ) : (
            <>
              {isEditing ? (
                <Textarea
                  ref={textareaRef}
                  value={slideOutline.content || ""}
                  onChange={handleTextareaChange}
                  onBlur={() => setIsEditing(false)}
                  placeholder="여기에 마크다운 내용을 입력하세요…"
                  className="min-h-[140px] resize-none overflow-hidden rounded-[8px] border-[#D8D8DF] bg-[#FBFBFC] px-3 py-3 font-mono text-[13px] leading-6 text-[#191919] shadow-none focus-visible:border-[#7A5AF8] focus-visible:ring-2 focus-visible:ring-[#7A5AF8]/20"
                />
              ) : (
                <div
                  role="button"
                  tabIndex={0}
                  aria-label={`슬라이드 ${index} 마크다운 편집`}
                  onClick={() => setIsEditing(true)}
                  onFocus={() => setIsEditing(true)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setIsEditing(true);
                    }
                  }}
                  className="block min-h-[60px] w-full rounded-[8px] px-0 py-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7A5AF8]/25"
                >
                  {previewHtml ? (
                    <div
                      className="text-sm sm:text-base flex-1 font-normal prose prose-sm max-w-none"
                      dangerouslySetInnerHTML={{ __html: previewHtml }}
                    />
                  ) : (
                    <p className="text-sm text-[#6B6B73]">
                      빈 개요
                    </p>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div className="hidden group-hover:flex absolute -top-3 -right-3 gap-1 sm:gap-2 items-center">
          <ToolTip content="슬라이드 삭제">
            <button
              onClick={handleSlideDelete}
              className="p-1.5 sm:p-2 bg-white shadow-md  rounded-full transition-colors"
            >
              <Trash className="w-4 h-4  text-black/70" />
            </button>
          </ToolTip>
        </div>
      </div>
    </div>
  );
}
