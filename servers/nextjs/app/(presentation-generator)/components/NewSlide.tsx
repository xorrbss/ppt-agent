"use client";
import React, {
  useEffect,
  useState,
  memo,
  useCallback,
  useRef,
} from "react";
import { useDispatch } from "react-redux";
import { addNewSlide } from "@/store/slices/presentationGeneration";
import { Loader2, X } from "lucide-react";
import { v4 as uuidv4 } from "uuid";
import { notify } from "@/components/ui/sonner";
import { getCustomTemplateDetails } from "@/app/hooks/useCustomTemplates";
import { getTemplatesByTemplateName } from "@/app/presentation-templates";

interface LayoutItemProps {
  layout: any;
  onSelect: (sampleData: any, layoutId: string) => void;
}

const PREVIEW_WIDTH = 1280;
const PREVIEW_HEIGHT = 720;

const LayoutItem = memo(({ layout, onSelect }: LayoutItemProps) => {
  const previewRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(0.2);
  const {
    component: LayoutComponent,
    sampleData,
    layoutId,
    layoutName,
  } = layout;

  useEffect(() => {
    if (!previewRef.current) return;

    const previewElement = previewRef.current;
    const updateScale = () => {
      const nextScale = Math.min(
        previewElement.clientWidth / PREVIEW_WIDTH,
        previewElement.clientHeight / PREVIEW_HEIGHT
      );
      setScale(nextScale || 0.2);
    };

    const resizeObserver = new ResizeObserver(updateScale);
    resizeObserver.observe(previewElement);
    updateScale();

    return () => resizeObserver.disconnect();
  }, []);

  const selectLayout = () => onSelect(sampleData, layoutId);

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${layoutName || "슬라이드"} 레이아웃 추가`}
      title={layoutName || "슬라이드 레이아웃"}
      onClick={selectLayout}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        selectLayout();
      }}
      className="relative aspect-video cursor-pointer overflow-hidden rounded-md border border-[#E4E4EA] bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04)] outline-none transition duration-200 hover:border-[#7C51F8] hover:shadow-[0_0_0_2px_rgba(124,81,248,0.18)] focus-visible:ring-2 focus-visible:ring-[#7C51F8]"
    >
      <div className="absolute inset-0 z-40 bg-transparent" />
      <div ref={previewRef} className="relative h-full w-full overflow-hidden">
        <div
          className="absolute left-0 top-0"
          style={{
            width: PREVIEW_WIDTH,
            height: PREVIEW_HEIGHT,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
          }}
        >
          <LayoutComponent data={sampleData} />
        </div>
      </div>
    </div>
  );
});

LayoutItem.displayName = "LayoutItem";
interface NewSlideV1Props {
  setShowNewSlideSelection: (show: boolean) => void;
  templateID: string;
  index: number;
  presentationId: string;
}
const NewSlideV1 = ({
  setShowNewSlideSelection,
  templateID,
  index,
  presentationId,
}: NewSlideV1Props) => {
  const dispatch = useDispatch();
  const [layouts, setLayouts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const isCustomTemplate = templateID.startsWith("custom-");

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowNewSlideSelection(false);
    };

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [setShowNewSlideSelection]);

  const handleNewSlide = useCallback(
    (sampleData: any, id: string) => {
      try {
        const newSlide = {
          id: uuidv4(),
          index: index,
          content: sampleData,
          layout_group: templateID,
          layout: isCustomTemplate ? `${templateID}:${id}` : id,
          presentation: presentationId,
        };
        dispatch(addNewSlide({ slideData: newSlide, index }));
        setShowNewSlideSelection(false);
      } catch (error: any) {
        console.error(error);
        notify.error("슬라이드를 추가할 수 없습니다", "새 슬라이드를 추가하는 중 문제가 발생했습니다.");
      }
    },
    [
      index,
      templateID,
      presentationId,
      dispatch,
      setShowNewSlideSelection,
      isCustomTemplate,
    ]
  );

  useEffect(() => {
    let isMounted = true;

    const fetchLayouts = async () => {
      try {
        setLoading(true);
        if (isCustomTemplate) {
          const customTemplateId = templateID.split("custom-")[1];
          const templateDetails = await getCustomTemplateDetails(
            customTemplateId,
            "사용자 지정 템플릿",
            "사용자가 만든 템플릿"
          );
          if (isMounted) setLayouts(templateDetails?.layouts || []);
        } else {
          const templateDetails = getTemplatesByTemplateName(templateID);
          if (isMounted) setLayouts(templateDetails || []);
        }
      } catch (error) {
        console.error("Error loading slide layouts:", error);
        if (isMounted) setLayouts([]);
      } finally {
        if (isMounted) setLoading(false);
      }
    };

    fetchLayouts();

    return () => {
      isMounted = false;
    };
  }, [isCustomTemplate, templateID]);

  const layoutCountText = `레이아웃 ${layouts.length}개`;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="choose-slide-layout-title"
      className="relative w-full rounded-[14px] border border-[#EDEEEF] bg-white font-syne shadow-[0_18px_60px_rgba(15,23,42,0.18)]"
    >
      <button
        type="button"
        aria-label="레이아웃 선택 닫기"
        onClick={() => setShowNewSlideSelection(false)}
        className="absolute right-0 top-[-52px] z-50 flex h-10 w-10 items-center justify-center rounded-full border border-[#EDEEEF] bg-white text-[#191919] shadow-[0_6.6px_13.2px_rgba(0,0,0,0.10)] transition hover:bg-[#F7F6F9]"
      >
        <X className="h-5 w-5" />
      </button>

      <div className="flex min-h-[64px] items-center justify-between border-b border-[#EDEEEF] px-5 py-4 md:px-6">
        <div>
          <h2
            id="choose-slide-layout-title"
            className="text-base font-medium leading-tight text-[#191919]"
          >
            슬라이드 레이아웃 선택
          </h2>
          <p className="mt-1 text-xs font-normal leading-none text-[#7A7A85]">
            {loading ? "레이아웃 불러오는 중…" : layoutCountText}
          </p>
        </div>
        {loading && (
          <Loader2 className="h-5 w-5 animate-spin text-[#7C51F8]" />
        )}
      </div>

      <div className="max-h-[min(70vh,640px)] overflow-y-auto px-4 py-4 md:px-5">
        {loading ? (
          <div className="flex h-56 items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-[#7C51F8]" />
          </div>
        ) : layouts.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {layouts.map((layout: any) => (
              <LayoutItem
                key={layout.layoutId}
                layout={layout}
                onSelect={handleNewSlide}
              />
            ))}
          </div>
        ) : (
          <div className="flex h-56 items-center justify-center rounded-lg border border-dashed border-[#D9D9E1] bg-[#FAFAFB] text-sm text-[#7A7A85]">
            사용할 수 있는 레이아웃이 없습니다.
          </div>
        )}
      </div>
    </div>
  );
};

export default NewSlideV1;
