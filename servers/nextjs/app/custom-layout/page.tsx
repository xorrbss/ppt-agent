"use client";

import React, { useState, useCallback, useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { toast } from "sonner";
import { Upload, FileText, X, Loader2 } from "lucide-react";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";
import { v4 as uuidv4 } from "uuid";
import EachSlide from "./components/EachSlide";
import FontManager from "./components/FontManager";
import Header from "@/components/Header";
import { useLayout } from "../(presentation-generator)/context/LayoutContext";

// Types
interface SlideData {
  slide_number: number;
  screenshot_url: string;
  xml_content: string;
}

interface UploadedFont {
  fontName: string;
  fontUrl: string;
  fontPath: string;
}

interface ProcessedSlide extends SlideData {
  html?: string;
  uploaded_fonts?: string[];
  processing?: boolean;
  processed?: boolean;
  error?: string;
  modified?: boolean; // Added for unsaved changes
}

interface FontData {
  internally_supported_fonts: {
    name: string;
    google_fonts_url: string;
  }[];
  not_supported_fonts: string[];
}

const CustomLayoutPage = () => {
  const { refetch } = useLayout();
  // State management
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isProcessingPptx, setIsProcessingPptx] = useState(false);
  const [slides, setSlides] = useState<ProcessedSlide[]>([]);
  const [isSavingLayout, setIsSavingLayout] = useState(false);
  const [isLayoutSaved, setIsLayoutSaved] = useState(false);
  const [UploadedFonts, setUploadedFonts] = useState<UploadedFont[]>([]);
  const [fontsData, setFontsData] = useState<FontData | null>(null);

  console.log(slides);

  // Load uploaded fonts dynamically
  useEffect(() => {
    UploadedFonts.forEach((font) => {
      // Check if font style already exists
      const existingStyle = document.querySelector(
        `style[data-font-url="${font.fontUrl}"]`
      );
      if (!existingStyle) {
        const style = document.createElement("style");
        style.setAttribute("data-font-url", font.fontUrl);

        // Use the actual font name for font-family
        style.textContent = `
          @font-face {
            font-family: '${font.fontName}';
            src: url('${font.fontUrl}') format('truetype');
            font-display: swap;
          }
        `;
        document.head.appendChild(style);
      }
    });
  }, [UploadedFonts]);

  // Load Google Fonts from fontsData
  useEffect(() => {
    if (fontsData?.internally_supported_fonts) {
      fontsData.internally_supported_fonts.forEach((font) => {
        // Check if font link already exists
        const existingFont = document.querySelector(
          `link[href="${font.google_fonts_url}"]`
        );
        // Only add if font doesn't already exist
        if (!existingFont) {
          const link = document.createElement("link");
          link.href = font.google_fonts_url;
          link.rel = "stylesheet";
          document.head.appendChild(link);
        }
      });
    }
  }, [fontsData]);

  // Warning before page unload
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      return "You have unsaved changes. Are you sure you want to leave?";
    };
    if (slides.length > 0 && !isLayoutSaved) {
      window.addEventListener("beforeunload", handleBeforeUnload);
    }
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [slides, isLayoutSaved]);
  // Font management functions
  const uploadFont = useCallback(
    async (fontName: string, file: File): Promise<string | null> => {
      // Check if font is already uploaded
      const existingFont = UploadedFonts.find((f) => f.fontName === fontName);
      if (existingFont) {
        toast.info(`Font "${fontName}" is already uploaded`);
        return existingFont.fontUrl;
      }

      // Validate file type
      const validExtensions = [".ttf", ".otf", ".woff", ".woff2", ".eot"];
      const fileExtension = file.name
        .toLowerCase()
        .substring(file.name.lastIndexOf("."));

      if (!validExtensions.includes(fileExtension)) {
        toast.error(
          "Invalid font file type. Please upload .ttf, .otf, .woff, .woff2, or .eot files"
        );
        return null;
      }

      // Validate file size (10MB limit)
      const maxSize = 10 * 1024 * 1024; // 10MB
      if (file.size > maxSize) {
        toast.error("Font file size must be less than 10MB");
        return null;
      }

      try {
        const formData = new FormData();
        formData.append("font_file", file);

        const response = await fetch("/api/v1/ppt/fonts/upload", {
          method: "POST",
          body: formData,
        });

        if (!response.ok) {
          throw new Error(`Upload failed: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.success) {
          const newFont: UploadedFont = {
            fontName: data.font_name || fontName,
            fontUrl: data.font_url,
            fontPath: data.font_path,
          };

          setUploadedFonts((prev) => [...prev, newFont]);
          toast.success(`Font "${fontName}" uploaded successfully`);
          return newFont.fontUrl;
        } else {
          throw new Error(data.message || "Upload failed");
        }
      } catch (error) {
        console.error("Error uploading font:", error);
        toast.error(`Failed to upload font "${fontName}"`, {
          description:
            error instanceof Error
              ? error.message
              : "An unexpected error occurred",
        });
        return null;
      }
    },
    [UploadedFonts]
  );

  const removeFont = useCallback((fontUrl: string) => {
    setUploadedFonts((prev) => prev.filter((font) => font.fontUrl !== fontUrl));

    // Remove the style element for this font
    const styleElement = document.querySelector(
      `style[data-font-url="${fontUrl}"]`
    );
    if (styleElement) {
      styleElement.remove();
    }

    toast.info("Font removed globally");
  }, []);

  const getAllUnsupportedFonts = useCallback((): string[] => {
    if (!fontsData?.not_supported_fonts) {
      return [];
    }
    return fontsData.not_supported_fonts;
  }, [fontsData]);

  // Save layout functionality
  const saveLayout = useCallback(async () => {
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
      setSlides((prevSlides) =>
        prevSlides.map((slide) => ({ ...slide, modified: false }))
      );

      toast.success(`Layout saved successfully`);
      refetch();
      setIsLayoutSaved(true);
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
  }, [slides, UploadedFonts]);

  // File upload handler
  const handleFileSelect = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;

      // Validate file type
      if (!file.name.toLowerCase().endsWith(".pptx")) {
        toast.error("Please select a valid PPTX file");
        return;
      }

      // Validate file size (50MB limit)
      const maxSize = 50 * 1024 * 1024; // 50MB
      if (file.size > maxSize) {
        toast.error("File size must be less than 50MB");
        return;
      }

      setSelectedFile(file);
      setSlides([]);
    },
    []
  );

  // Remove selected file
  const removeFile = useCallback(() => {
    setSelectedFile(null);
    setSlides([]);
  }, []);

  // Process individual slide to HTML
  const processSlideToHtml = useCallback(
    async (slide: SlideData, index: number) => {
      setIsLayoutSaved(false);
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

      // const pptxData = processData;
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

      // Start processing first slide
      setTimeout(() => {
        console.log("Starting to process first slide");
        processSlideToHtml(initialSlides[0], 0);
      }, 500);
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
  }, [selectedFile, processSlideToHtml]);

  // Retry failed slide
  const retrySlide = useCallback(
    (index: number) => {
      setIsLayoutSaved(false);
      const slide = slides[index];
      if (slide) {
        processSlideToHtml(slide, index);
      }
    },
    [slides, processSlideToHtml]
  );

  // Mark slide as modified when it's updated
  const handleSlideUpdate = useCallback(
    (index: number, updatedSlideData: any) => {
      setIsLayoutSaved(false);
      setSlides((prevSlides) =>
        prevSlides.map((s, i) =>
          i === index
            ? {
                ...s,
                ...updatedSlideData,
                modified: true,
              }
            : s
        )
      );
    },
    []
  );

  // Calculate progress
  const completedSlides = slides.filter(
    (slide) => slide.processed || slide.error
  ).length;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 ">
      <Header />
      <div className="max-w-[1440px] aspect-video mx-auto px-6 ">
        {/* Header */}
        <div className="text-center space-y-2 my-6">
          <h1 className="text-4xl font-bold text-gray-900">
            Custom Layout Processor
          </h1>
          <p className="text-lg text-gray-600 max-w-2xl mx-auto">
            Upload your PPTX file to extract slides and convert them to
            interactive HTML layouts
          </p>
        </div>

        {/* Upload Section */}
        <Card className="w-full">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Upload className="w-5 h-5" />
              Upload PPTX File
            </CardTitle>
            <CardDescription>
              Select a PowerPoint file (.pptx) to process. Maximum file size:
              50MB
            </CardDescription>
            {slides.length > 0 && (
              <div className="flex items-center justify-end gap-2">
                {slides.some((s) => s.processing) && (
                  <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
                )}
                {completedSlides}/{slides.length} slides completed
              </div>
            )}
          </CardHeader>
          <CardContent className="space-y-4">
            {!selectedFile ? (
              <div className="border-2 relative border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-gray-400 transition-colors">
                <Upload className="w-12 h-12 text-gray-400 mx-auto mb-4" />
                <Label htmlFor="file-upload" className="cursor-pointer">
                  <span className="text-lg font-medium text-gray-700">
                    Click to upload a PPTX file
                  </span>
                  <input
                    id="file-upload"
                    type="file"
                    accept=".pptx"
                    onChange={handleFileSelect}
                    className="opacity-0 w-full h-full cursor-pointer absolute top-0 left-0 z-10"
                  />
                </Label>
                <p className="text-sm text-gray-500 mt-2">
                  Drag and drop your file here or click to browse
                </p>
              </div>
            ) : (
              <div className="flex items-center justify-between p-4 bg-blue-50 border border-blue-200 rounded-lg">
                <div className="flex items-center gap-3">
                  <FileText className="w-8 h-8 text-blue-600" />
                  <div>
                    <p className="font-medium text-gray-900">
                      {selectedFile.name}
                    </p>
                    <p className="text-sm text-gray-500">
                      {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB
                    </p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={removeFile}
                  disabled={
                    isProcessingPptx || slides.some((s) => s.processing)
                  }
                  className="text-red-600 hover:text-red-700 hover:bg-red-50"
                >
                  <X className="w-4 h-4" />
                </Button>
              </div>
            )}

            <div className="flex gap-3">
              <Button
                onClick={processFile}
                disabled={isProcessingPptx || slides.some((s) => s.processing)}
                className="flex-1 bg-blue-600 hover:bg-blue-700 text-white"
              >
                {isProcessingPptx
                  ? "Extracting Slides..."
                  : !selectedFile
                  ? "Select a PPTX file"
                  : "Process File"}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Global Font Management */}
        {fontsData && (
          <FontManager
            fontsData={fontsData}
            UploadedFonts={UploadedFonts}
            uploadFont={uploadFont}
            removeFont={removeFont}
            getAllUnsupportedFonts={getAllUnsupportedFonts}
          />
        )}

        {/* Slides Section */}
        {slides.length > 0 && (
          <div className="space-y-6 mt-10">
            {slides.map((slide, index) => (
              <EachSlide
                key={index}
                slide={slide}
                index={index}
                isProcessingPptx={isProcessingPptx}
                retrySlide={retrySlide}
                setSlides={setSlides}
                onSlideUpdate={(updatedSlideData) =>
                  handleSlideUpdate(index, updatedSlideData)
                }
              />
            ))}
          </div>
        )}

        {/* Floating Save Layout Button */}
        {slides.length > 0 && slides.some((s) => s.processed) && (
          <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
            <Button
              onClick={saveLayout}
              disabled={isSavingLayout || isProcessingPptx}
              className="bg-green-600 hover:bg-green-700 text-white shadow-lg hover:shadow-xl transition-all duration-200 px-10 py-3 text-lg"
              size="lg"
            >
              {isSavingLayout ? (
                <>
                  <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                  Saving Layout...
                </>
              ) : (
                <>
                  <FileText className="w-5 h-5 mr-2" />
                  Save Layout
                </>
              )}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
};

export default CustomLayoutPage;
