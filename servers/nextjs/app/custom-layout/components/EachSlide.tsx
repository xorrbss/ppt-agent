import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { AlertCircle, CheckCircle, Edit, Loader2 } from "lucide-react";
import React, { useState, useEffect, useRef } from "react";
import ToolTip from "@/components/ToolTip";
import DrawingCanvas from "./DrawingCanvas";

const EachSlide = ({
  slide,
  index,
  retrySlide,
  setSlides,
  onSlideUpdate,
}: {
  slide: any;
  index: number;
  retrySlide: (index: number) => void;
  setSlides: React.Dispatch<React.SetStateAction<any[]>>;
  onSlideUpdate?: (updatedSlideData: any) => void;
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

  const handleEditClick = () => {
    setShowDrawingCanvas(true);
  };

  const handleCloseDrawingCanvas = () => {
    setShowDrawingCanvas(false);
  };

  const handleSlideUpdate = (updatedSlideData: any) => {
    if (onSlideUpdate) {
      onSlideUpdate(updatedSlideData);
    } else {
      // Fallback to original behavior
      setSlides((prevSlides) =>
        prevSlides.map((s, i) =>
          i === index ? { ...s, ...updatedSlideData } : s
        )
      );
    }
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
          onSlideUpdate={handleSlideUpdate}
        />
      )}
    </>
  );
};

export default EachSlide;
