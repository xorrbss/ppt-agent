import { useState, useCallback } from "react";
import { toast } from "sonner";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";
import { ProcessedSlide, SlideData, FontData } from "../types";

export const useSlideProcessing = (
  selectedFile: File | null,
  slides: ProcessedSlide[],
  setSlides: React.Dispatch<React.SetStateAction<ProcessedSlide[]>>,
  
  setFontsData: React.Dispatch<React.SetStateAction<FontData | null>>
) => {
  const [isProcessingPptx, setIsProcessingPptx] = useState(false);

  // Process individual slide to HTML
  const processSlideToHtml = useCallback(
    async (slide: SlideData, index: number) => {
      console.log(
        `Starting to process slide ${slide.slide_number} at index ${index}`
      );

      // Update slide to processing state
      setSlides((prev) =>
        prev.map((s, i) =>
          i === index ? { ...s, processing: true, error: undefined } : s
        )
      );

      try {
        const htmlResponse = await fetch("/api/v1/ppt/slide-to-html/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            image: slide.screenshot_url,
            xml: slide.xml_content,
            fonts: slide.normalized_fonts ?? [],
          }),
        });

        const htmlData = await ApiResponseHandler.handleResponse(
          htmlResponse,
          `Failed to convert slide ${slide.slide_number} to HTML`
        );

        console.log(`Successfully processed slide ${slide.slide_number}`);
        // Update slide with success
        setSlides((prev) => {
          const newSlides = prev.map((s, i) =>
            i === index
              ? {
                  ...s,
                  processing: false,
                  processed: true,
                  html: htmlData.html,
                }
              : s
          );

          // Process next slide if available
          const nextIndex = index + 1;
          if (
            nextIndex < newSlides.length &&
            !newSlides[nextIndex].processed &&
            !newSlides[nextIndex].processing
          ) {
            console.log(
              `Scheduling next slide ${nextIndex + 1} for processing`
            );
            setTimeout(() => {
              const nextSlide = newSlides[nextIndex];
              processSlideToHtml(nextSlide, nextIndex);
            }, 1000); // 1 second delay between slides
          }

          return newSlides;
        });
      } catch (error) {
        console.error(`Error processing slide ${slide.slide_number}:`, error);
        const errorMessage =
          error instanceof Error ? error.message : "Failed to convert to HTML";

        // Update slide with error
        setSlides((prev) => {
          const newSlides = prev.map((s, i) =>
            i === index
              ? {
                  ...s,
                  processing: false,
                  processed: false,
                  error: errorMessage,
                }
              : s
          );

          // Continue with next slide even if this one failed
          const nextIndex = index + 1;
          if (
            nextIndex < newSlides.length &&
            !newSlides[nextIndex].processed &&
            !newSlides[nextIndex].processing
          ) {
            console.log(`Scheduling next slide ${nextIndex + 1} after error`);
            setTimeout(() => {
              const nextSlide = newSlides[nextIndex];
              processSlideToHtml(nextSlide, nextIndex);
            }, 1000);
          }

          return newSlides;
        });
      }
    },
    []
  );

  // Process PPTX file to extract slides
  const processFile = useCallback(async () => {
    if (!selectedFile) {
      toast.error("Please select a PPTX file first");
      return;
    }

    try {
      setIsProcessingPptx(true);

      const formData = new FormData();
      formData.append("pptx_file", selectedFile);

      const pptxResponse = await fetch("/api/v1/ppt/pptx-slides/process", {
        method: "POST",
        body: formData,
      });
      const pptxData = await ApiResponseHandler.handleResponse(
        pptxResponse,
        "Failed to process PPTX file"
      );

      if (!pptxData.success || !pptxData.slides?.length) {
        throw new Error("No slides found in the PPTX file");
      }

      // Extract fonts data from the response
      if (pptxData.fonts) {
        setFontsData(pptxData.fonts);
      }

      // Initialize slides with skeleton state
      const initialSlides: ProcessedSlide[] = pptxData.slides.map(
        (slide: any) => ({
          ...slide,
          processing: false,
          processed: false,
        })
      );

      setSlides(initialSlides);

      toast.success(
        `Successfully extracted ${pptxData.slides.length} slides! Converting to HTML...`
      );

      
    } catch (error) {
      console.error("Error processing file:", error);
      const errorMessage =
        error instanceof Error ? error.message : "An unexpected error occurred";
      toast.error("Processing failed", {
        description: errorMessage,
      });
    } finally {
      setIsProcessingPptx(false);
    }
  }, [selectedFile, processSlideToHtml, setSlides, setFontsData]);

  // Retry failed slide
  const retrySlide = useCallback(
    (index: number) => {
      const slide = slides[index];
      if (slide) {
        processSlideToHtml(slide, index);
      }
    },
    [slides, processSlideToHtml]
  );

  return {
    isProcessingPptx,
    processFile,
    processSlideToHtml,
    retrySlide,
  };
}; 