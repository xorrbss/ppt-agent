import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  AlertCircle,
  CheckCircle,
  Edit,
  Loader2,
  SendHorizontal,
  WandSparkles,
} from "lucide-react";
import React, { useState, useEffect, useRef } from "react";
import { Textarea } from "@/components/ui/textarea";
import ToolTip from "@/components/ToolTip";
import DrawingCanvas from "./DrawingCanvas";

const EachSlide = ({
  slide,
  index,
  retrySlide,
  setSlides,
}: {
  slide: any;
  index: number;
  retrySlide: (index: number) => void;
  setSlides: (slides: any[]) => void;
}) => {
  const [isUpdating, setIsUpdating] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [showDrawingCanvas, setShowDrawingCanvas] = useState(false);
  const slideContentRef = useRef<HTMLDivElement>(null);

  // Load Tailwind CSS dynamically for slide content
  useEffect(() => {
    if (slide.processed && slide.html) {
      // Check if Tailwind CSS is already loaded
      const existingScript = document.querySelector(
        'script[src*="tailwindcss.com"]'
      );
      if (!existingScript) {
        const script = document.createElement("script");
        script.src = "https://cdn.tailwindcss.com";
        script.async = true;
        document.head.appendChild(script);
      }
    }
  }, [slide.processed, slide.html]);

  const handleSubmit = async () => {
    setIsUpdating(true);
    try {
      const response = await fetch("/api/update-slide", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ slide_number: slide.slide_number, prompt }),
      });
      const data = await response.json();
      console.log(data);
    } catch (error) {
      console.error("Error updating slide:", error);
    } finally {
      setIsUpdating(false);
    }
  };

  const handleEditClick = () => {
    setShowDrawingCanvas(true);
  };

  const handleCloseDrawingCanvas = () => {
    setShowDrawingCanvas(false);
  };

  return (
    <>
      <Card key={slide.slide_number} className="border-2 w-full relative">
        <CardHeader className="pb-4">
          <CardTitle className="text-xl flex items-center justify-between">
            {slide.processing ? (
              <Loader2 className="w-6 h-6 text-blue-600 animate-spin" />
            ) : slide.processed ? (
              <CheckCircle className="w-6 h-6 text-green-600" />
            ) : slide.error ? (
              <AlertCircle className="w-6 h-6 text-red-600" />
            ) : (
              <div className="w-6 h-6 border-2 border-gray-300 rounded-full" />
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Slide Content */}
          {slide.processing ? (
            <div className="space-y-4">
              <p className="text-base text-blue-600 font-medium">
                🔄 Converting to HTML...
              </p>
              <div className="animate-pulse space-y-3">
                <div className="h-6 bg-gray-200 rounded w-2/3"></div>
                <div className="h-6 bg-gray-200 rounded w-1/2"></div>
                <div className="h-64 bg-gray-200 rounded"></div>
              </div>
            </div>
          ) : slide.processed && slide.html ? (
            <div
              ref={slideContentRef}
              className="relative "
              dangerouslySetInnerHTML={{
                __html: slide.html,
              }}
            ></div>
          ) : slide.error ? (
            <div className="space-y-4">
              <p className="text-base text-red-600 font-medium">
                ✗ Conversion failed
              </p>
              <div className="text-sm text-gray-700 p-4 bg-red-50 rounded border border-red-200">
                {slide.error.includes("image exceeds 5 MB maximum") ? (
                  <div>
                    <p className="font-medium text-red-700 mb-2">
                      Image too large for processing
                    </p>
                    <p>
                      This slide's image exceeds the 5MB limit. Try using a
                      smaller resolution PPTX file.
                    </p>
                  </div>
                ) : (
                  slide.error
                )}
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => retrySlide(index)}
                disabled={slide.processing}
                className="w-full text-red-600 hover:text-red-700 hover:bg-red-50"
              >
                🔄 Retry Conversion
              </Button>
            </div>
          ) : (
            <div className="space-y-4">
              <p className="text-base text-gray-500">
                ⏳ Waiting in queue to process...
              </p>
              <div className="animate-pulse space-y-3">
                <div className="h-6 bg-gray-200 rounded w-2/3"></div>
                <div className="h-6 bg-gray-200 rounded w-1/2"></div>
                <div className="h-64 bg-gray-200 rounded"></div>
              </div>
            </div>
          )}
        </CardContent>
        <Button
          onClick={() => {
            const newWindow = window.open("", "_blank");
            if (newWindow) {
              newWindow.document.write(`
              <!DOCTYPE html>
              <html lang="en">
              <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Slide ${slide.slide_number} - HTML Preview</title>
                <script src="https://cdn.tailwindcss.com"></script>
                
              </head>
              <body>
                <div class="slide-container">
                  ${slide.html}
                </div>
              </body>
              </html>`);
            }
          }}
          variant="outline"
          size="sm"
        >
          Open in new tab
        </Button>
        <div className="absolute top-2 z-20 sm:top-4 hidden md:block left-2 sm:left-4 transition-transform">
          <Popover>
            <PopoverTrigger>
              <ToolTip content="Update slide using prompt">
                <div
                  className={`p-2 group-hover:scale-105 rounded-lg bg-[#5141e5] hover:shadow-md transition-all duration-300 cursor-pointer shadow-md `}
                >
                  <WandSparkles className="w-4 sm:w-5 h-4 sm:h-5 text-white" />
                </div>
              </ToolTip>
            </PopoverTrigger>
            <PopoverContent
              side="right"
              align="start"
              sideOffset={10}
              className="w-[280px] sm:w-[400px] z-20"
            >
              <div className="space-y-4">
                <form
                  className="flex flex-col gap-3"
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleSubmit();
                  }}
                >
                  <Textarea
                    id={`slide-${slide.index}-prompt`}
                    placeholder="Enter your prompt here..."
                    className="w-full min-h-[100px] max-h-[100px] p-2 text-sm border rounded-lg focus:outline-none focus-visible:ring-0 focus-visible:ring-offset-0"
                    disabled={isUpdating}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSubmit();
                      }
                    }}
                    rows={4}
                    wrap="soft"
                  />
                  <button
                    disabled={isUpdating}
                    type="submit"
                    className={`bg-gradient-to-r from-[#9034EA] to-[#5146E5] rounded-[32px] px-4 py-2 text-white flex items-center justify-end gap-2 ml-auto ${
                      isUpdating ? "opacity-70 cursor-not-allowed" : ""
                    }`}
                  >
                    {isUpdating ? "Updating..." : "Update"}
                    <SendHorizontal className="w-4 sm:w-5 h-4 sm:h-5" />
                  </button>
                </form>
              </div>
            </PopoverContent>
          </Popover>
        </div>
        <div className="absolute top-2 z-20 sm:top-4 hidden md:block left-2 sm:left-16 transition-transform">
          <ToolTip content="Edit slide">
            <div
              onClick={handleEditClick}
              className={`p-2 group-hover:scale-105 rounded-lg bg-[#5141e5] hover:shadow-md transition-all duration-300 cursor-pointer shadow-md `}
            >
              <Edit className="w-4 sm:w-5 h-4 sm:h-5 text-white" />
            </div>
          </ToolTip>
        </div>
      </Card>

      {/* Drawing Canvas Modal */}
      {showDrawingCanvas && slideContentRef.current && (
        <DrawingCanvas
          slideElement={slideContentRef.current}
          onClose={handleCloseDrawingCanvas}
          slideNumber={slide.slide_number}
        />
      )}
    </>
  );
};

export default EachSlide;
