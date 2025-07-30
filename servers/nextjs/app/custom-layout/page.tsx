"use client";

import React, { useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { toast } from "sonner";
import { Upload, FileText, X } from "lucide-react";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";

// Types
import EachSlide from "./components/EachSlide";
import { firstSlide, processData, slide2, slide3, slide4 } from "./data";
interface SlideData {
  slide_number: number;
  screenshot_url: string;
  xml_content: string;
}

interface ProcessedSlide extends SlideData {
  html?: string;
  processing?: boolean;
  processed?: boolean;
  error?: string;
}

const CustomLayoutPage = () => {
  // State management
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isProcessingPptx, setIsProcessingPptx] = useState(false);
  const [slides, setSlides] = useState<ProcessedSlide[]>([]);

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
        // const htmlResponse = await fetch("/api/v1/ppt/slide-to-html/", {
        //   method: "POST",
        //   headers: {
        //     "Content-Type": "application/json",
        //   },
        //   body: JSON.stringify({
        //     image: slide.screenshot_url,
        //     xml: slide.xml_content,
        //   }),
        // });
        let htmlResponse: any;
        if (index === 0) {
          htmlResponse = firstSlide;
        } else if (index === 1) {
          htmlResponse = slide2;
        } else if (index === 2) {
          htmlResponse = slide3;
        } else {
          htmlResponse = slide4;
        }

        // const htmlData: SlideToHtmlResponse =
        //   await ApiResponseHandler.handleResponse(
        //     htmlResponse,
        //     `Failed to convert slide ${slide.slide_number} to HTML`
        //   );

        const htmlData = htmlResponse;

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

      // const pptxResponse = await fetch("/api/v1/ppt/pptx-slides/process", {
      //   method: "POST",
      //   body: formData,
      // });
      const pptxResponse = processData;
      const pptxData = pptxResponse;

      // const pptxData: PptxProcessResponse =
      //   await ApiResponseHandler.handleResponse(
      //     pptxResponse,
      //     "Failed to process PPTX file"
      //   );

      // if (!pptxData.success || !pptxData.slides?.length) {
      //   throw new Error("No slides found in the PPTX file");
      // }

      // Initialize slides with skeleton state
      const initialSlides: ProcessedSlide[] = pptxData.slides.map((slide) => ({
        ...slide,
        processing: false,
        processed: false,
      }));

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
      const slide = slides[index];
      if (slide) {
        processSlideToHtml(slide, index);
      }
    },
    [slides, processSlideToHtml]
  );

  // Calculate progress
  const completedSlides = slides.filter(
    (slide) => slide.processed || slide.error
  ).length;
  const progressPercentage =
    slides.length > 0 ? Math.round((completedSlides / slides.length) * 100) : 0;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 p-4">
      <div className="max-w-[1440px] aspect-video mx-auto px-6 py-8">
        {/* Header */}
        <div className="text-center space-y-2">
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
          </CardHeader>
          <CardContent className="space-y-4">
            {!selectedFile ? (
              <div className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-gray-400 transition-colors">
                <Upload className="w-12 h-12 text-gray-400 mx-auto mb-4" />
                <Label htmlFor="file-upload" className="cursor-pointer">
                  <span className="text-lg font-medium text-gray-700">
                    Click to upload a PPTX file
                  </span>
                  <Input
                    id="file-upload"
                    type="file"
                    accept=".pptx"
                    onChange={handleFileSelect}
                    className="hidden"
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

        {/* Progress Section */}
        {slides.length > 0 && (
          <Card className="mt-10">
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>Processing Progress</span>
                <span className="text-sm font-normal text-gray-600">
                  {completedSlides}/{slides.length} slides completed
                </span>
              </CardTitle>
              <CardDescription>
                Converting slides to HTML layouts...
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Progress value={progressPercentage} className="w-full" />
              <div className="flex justify-between text-sm text-gray-600 mt-2">
                <span>Progress: {progressPercentage}%</span>
                <span>{slides.length} total slides</span>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Slides Section */}
        {slides.length > 0 && (
          <div className="space-y-6 mt-10">
            {slides.map((slide, index) => (
              <EachSlide
                key={index}
                slide={slide}
                index={index}
                retrySlide={retrySlide}
                setSlides={setSlides}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default CustomLayoutPage;
