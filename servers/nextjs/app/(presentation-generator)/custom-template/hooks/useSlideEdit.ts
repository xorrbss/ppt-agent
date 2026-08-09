import { useState } from "react";
import { ProcessedSlide } from "../types";
import { getHeader } from "@/app/(presentation-generator)/services/api/header";
import { notify } from "@/components/ui/sonner";
import { getApiUrl } from "@/utils/api";

export const useSlideEdit = (
  slide: ProcessedSlide,
  index: number,
  onSlideUpdate?: (updatedSlideData: Partial<ProcessedSlide>) => void,
  setSlides?: React.Dispatch<React.SetStateAction<ProcessedSlide[]>>
) => {
  const [isEditMode, setIsEditMode] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);
  const [prompt, setPrompt] = useState("");

  const handleSave = async (): Promise<boolean> => {

    if (!prompt.trim()) {
      notify.warning("프롬프트를 입력해 주세요", "저장하기 전에 수정할 내용을 입력해야 합니다.");
      return false;
    }

    setIsUpdating(true);

    try {

      const response = await fetch(getApiUrl(`/api/v1/ppt/template/slide-layout/edit`), {
        method: "POST",
        body: JSON.stringify({
          prompt: prompt,
          react_component: slide.react ?? "",
        }),
        headers: getHeader(),
      });

      if (!response.ok) {
        throw new Error(`API call failed: ${response.statusText}`);
      }

      // EditSlideLayoutResponse: { react_component: str }
      const data = (await response.json()) as { react_component: string };
      const updatedSlideData: Partial<ProcessedSlide> = {
        slide_number: slide.slide_number,
        react: data.react_component,
        processed: true,
        processing: false,
        error: undefined,
      };


      if (onSlideUpdate) {
        onSlideUpdate(updatedSlideData);
      } else if (setSlides) {
        setSlides((prevSlides) =>
          prevSlides.map((s, i) =>
            i === index ? { ...s, ...updatedSlideData } : s
          )
        );
      }

      // Exit edit mode
      setIsEditMode(false);
      setPrompt("");
      return true;
    } catch (error) {
      console.error("Error updating slide:", error);
      notify.error(
        "슬라이드를 업데이트하지 못했습니다",
        error instanceof Error ? error.message : "알 수 없는 오류"
      );
      return false;
    } finally {
      setIsUpdating(false);
    }
  };

  const handleEditClick = () => {
    setIsEditMode(true);
  };

  const handleCancelEdit = () => {
    setIsEditMode(false);
    setPrompt("");
  };

  return {
    isEditMode,
    isUpdating,
    prompt,

    setPrompt,
    handleSave,
    handleEditClick,
    handleCancelEdit,
  };
};
