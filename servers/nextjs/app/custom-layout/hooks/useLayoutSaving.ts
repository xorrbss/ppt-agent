import { useState, useCallback } from "react";
import { toast } from "sonner";
import { v4 as uuidv4 } from "uuid";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";
import { ProcessedSlide, UploadedFont } from "../types";

export const useLayoutSaving = (
  slides: ProcessedSlide[],
  UploadedFonts: UploadedFont[],
  refetch: () => void
) => {
  const [isSavingLayout, setIsSavingLayout] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const openSaveModal = useCallback(() => {
    setIsModalOpen(true);
  }, []);

  const closeSaveModal = useCallback(() => {
    setIsModalOpen(false);
  }, []);

  const saveLayout = useCallback(async (layoutName: string, description: string) => {
    if (!slides.length) {
      toast.error("No slides to save");
      return;
    }

    setIsSavingLayout(true);

    try {
      // Convert each slide HTML to React component
      const reactComponents = [];
      const presentationId = uuidv4();

      // Get all uploaded font URLs
      const FontUrls = UploadedFonts.map((font) => font.fontUrl);
      console.log("FontUrls", FontUrls);

      for (let i = 0; i < slides.length; i++) {
        const slide = slides[i];

        if (!slide.html) {
          toast.error(`Slide ${slide.slide_number} has no HTML content`);
          continue;
        }

        try {
          const response = await fetch("/api/v1/ppt/html-to-react/", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              html: slide.html,
            }),
          });

          const data = await ApiResponseHandler.handleResponse(
            response,
            `Failed to convert slide ${slide.slide_number} to React`
          );

          reactComponents.push({
            presentation_id: presentationId,
            layout_id: `${slide.slide_number}`,
            layout_name: `Slide${slide.slide_number}`,
            layout_code: data.react_component || data.component_code,
            fonts: FontUrls,
          });

          // Update progress
          toast.info(
            `Converted slide ${slide.slide_number} to React component`
          );
        } catch (error) {
          console.error(`Error converting slide ${slide.slide_number}:`, error);
          toast.error(`Failed to convert slide ${slide.slide_number}`, {
            description:
              error instanceof Error
                ? error.message
                : "An unexpected error occurred",
          });
        }
      }

      if (reactComponents.length === 0) {
        toast.error("No slides were successfully converted");
        return;
      }
      console.log(reactComponents);

      // Save the layout components to the app_data/layouts folder
      const saveResponse = await fetch(
        "/api/v1/ppt/layout-management/save-layouts",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            layouts: reactComponents,
            layout_name: layoutName,
            description: description,
          }),
        }
      );

      const data = await ApiResponseHandler.handleResponse(
        saveResponse,
        "Failed to save layout components"
      );

      if (!data.success) {
        toast.error("Failed to save layout components");
        return;
      }

      toast.success("Layout saved successfully");

      // Mark all slides as saved (remove modified flag)
      slides.forEach((slide) => {
        slide.modified = false;
      });

      toast.success(`Layout "${layoutName}" saved successfully`);
      refetch();
      closeSaveModal();
    } catch (error) {
      console.error("Error saving layout:", error);
      toast.error("Failed to save layout", {
        description:
          error instanceof Error
            ? error.message
            : "An unexpected error occurred",
      });
    } finally {
      setIsSavingLayout(false);
    }
  }, [slides, UploadedFonts, refetch, closeSaveModal]);

  return {
    isSavingLayout,
    isModalOpen,
    openSaveModal,
    closeSaveModal,
    saveLayout,
  };
}; 