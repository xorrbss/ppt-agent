import { useState, useCallback } from "react";
import { notify } from "@/components/ui/sonner";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";
import { ProcessedSlide } from "../types";
import { getHeader } from "@/app/(presentation-generator)/services/api/header";
import { getApiUrl } from "@/utils/api";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";


export const useLayoutSaving = (
  slides: ProcessedSlide[],


) => {
  const [isSavingLayout, setIsSavingLayout] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const openSaveModal = useCallback(() => {
    trackEvent(MixpanelEvent.CustomTemplate_Save_Modal_Opened, {
      slide_count: slides.length,
      processed_slides: slides.filter((slide) => slide.processed).length,
    });
    setIsModalOpen(true);
  }, [slides]);

  const closeSaveModal = useCallback(() => {
    setIsModalOpen(false);
  }, []);




  const saveLayout = useCallback(async (layoutName: string, description: string, template_info_id: string): Promise<string | null> => {
    if (!slides.length) {
      notify.error("저장할 슬라이드가 없습니다", "레이아웃을 저장하기 전에 슬라이드를 하나 이상 추가하세요.");
      return null;
    }

    setIsSavingLayout(true);

    try {
      trackEvent(MixpanelEvent.CustomTemplate_Save_Started, {
        template_info_id,
        layout_name: layoutName,
        layout_name_length: layoutName.length,
        description_length: description.length,
        slide_count: slides.length,
        processed_slides: slides.filter((slide) => slide.processed).length,
      });



      const reactComponents = slides.map((slide) => ({
        layout_id: `${slide.slide_number}`,
        layout_name: `Slide${slide.slide_number}`,
        layout_code: slide.react,
      }));


      // Save the layout components to the app_data/layouts folder
      const saveResponse = await fetch(
        getApiUrl(`/api/v1/ppt/template/save`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify({
            template_info_id: template_info_id,
            name: layoutName,
            description: description,
            layouts: reactComponents,

          }),
        }
      );

      const data = await ApiResponseHandler.handleResponse(
        saveResponse,
        "레이아웃 저장 실패 components"
      );
      if (!data) {
        notify.error(
          "레이아웃을 저장하지 못했습니다",
          "일부 레이아웃 구성 요소를 저장하지 못했습니다. 다시 시도해 주세요."
        );
        return null;
      }

      // Mark all slides as saved (remove modified flag)
      slides.forEach((slide) => {
        slide.modified = false;
      });

      notify.success(
        "레이아웃이 저장되었습니다",
        `"${layoutName}" 레이아웃이 성공적으로 저장되었습니다.`
      );
      trackEvent(MixpanelEvent.CustomTemplate_Saved, {
        template_info_id,
        saved_template_id: data.id,
        layout_name: layoutName,
        slide_count: slides.length,
      });

      closeSaveModal();
      return data.id;
    } catch (error) {
      console.error("Error saving layout:", error);
      notify.error(
        "레이아웃 저장 실패",
        error instanceof Error
          ? error.message
          : "예상치 못한 오류가 발생했습니다"
      );
      return null;
    } finally {
      setIsSavingLayout(false);
    }
  }, [slides, closeSaveModal]);

  return {
    isSavingLayout,
    isModalOpen,
    openSaveModal,
    closeSaveModal,
    saveLayout,
  };
}; 
