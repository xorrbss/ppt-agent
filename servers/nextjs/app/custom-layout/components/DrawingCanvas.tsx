"use client";

import React, { useRef, useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  X,
  Pencil,
  Eraser,
  RotateCcw,
  Download,
  SendHorizontal,
} from "lucide-react";
import html2canvas from "html2canvas";

interface DrawingCanvasProps {
  slideElement: HTMLElement | null;
  onClose: () => void;
  slideNumber: number;
  onSlideUpdate: (updatedSlide: any) => void;
}

const DrawingCanvas: React.FC<DrawingCanvasProps> = ({
  slideElement,
  onClose,
  slideNumber,
  onSlideUpdate,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const slideDisplayRef = useRef<HTMLDivElement>(null);
  const slideContentRef = useRef<HTMLDivElement>(null);
  const [strokeWidth, setStrokeWidth] = useState(3);
  const [strokeColor, setStrokeColor] = useState("#000000");
  const [eraserMode, setEraserMode] = useState(false);
  const [isDrawing, setIsDrawing] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [isUpdating, setIsUpdating] = useState(false);
  const [slideHtml, setSlideHtml] = useState("");
  const [canvasDimensions, setCanvasDimensions] = useState({
    width: 800,
    height: 600,
  });

  useEffect(() => {
    if (slideElement && containerRef.current) {
      console.log("slideElement", slideElement);
      const rect = slideElement.getBoundingClientRect();

      // Set canvas dimensions to match the slide element
      setCanvasDimensions({
        width: Math.max(rect.width, 800),
        height: Math.max(rect.height, 600),
      });

      // Store the HTML once to prevent re-renders
      setSlideHtml(slideElement.innerHTML);
    }
  }, [slideElement]);

  // Apply optimizations once after slide content is rendered
  useEffect(() => {
    if (slideContentRef.current && slideHtml) {
      const slideContent = slideContentRef.current;

      // Apply styles to prevent interactions and flickering
      slideContent.style.pointerEvents = "none";
      slideContent.style.userSelect = "none";
      slideContent.style.transform = "translateZ(0)";
      slideContent.style.willChange = "auto";
      slideContent.style.backfaceVisibility = "hidden";

      // Target all interactive elements
      const interactiveElements = slideContent.querySelectorAll(
        "img, video, iframe, a, button, input, textarea, select"
      );

      interactiveElements.forEach((element) => {
        const el = element as HTMLElement;
        el.style.pointerEvents = "none";
        el.style.userSelect = "none";
        (el.style as any).webkitUserSelect = "none";
        (el.style as any).webkitTouchCallout = "none";
        (el.style as any).webkitUserDrag = "none";
        el.style.transform = "translateZ(0)";
        el.style.backfaceVisibility = "hidden";

        if (element.tagName === "IMG") {
          (element as HTMLImageElement).draggable = false;
        }

        // Remove any event listeners
        el.onclick = null;
        el.onmousedown = null;
        el.onmouseup = null;
        el.onmousemove = null;
      });
    }
  }, [slideHtml]);

  const getCanvasContext = () => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    return canvas.getContext("2d");
  };

  const getMousePos = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };

    const rect = canvas.getBoundingClientRect();
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    };
  };

  const getTouchPos = (e: React.TouchEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };

    const rect = canvas.getBoundingClientRect();
    const touch = e.touches[0];
    return {
      x: touch.clientX - rect.left,
      y: touch.clientY - rect.top,
    };
  };

  const startDrawing = useCallback(
    (pos: { x: number; y: number }) => {
      const ctx = getCanvasContext();
      if (!ctx) return;

      setIsDrawing(true);
      ctx.beginPath();
      ctx.moveTo(pos.x, pos.y);

      if (eraserMode) {
        ctx.globalCompositeOperation = "destination-out";
        ctx.lineWidth = strokeWidth * 2;
      } else {
        ctx.globalCompositeOperation = "source-over";
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = strokeWidth;
      }

      ctx.lineCap = "round";
      ctx.lineJoin = "round";
    },
    [eraserMode, strokeColor, strokeWidth]
  );

  const draw = useCallback(
    (pos: { x: number; y: number }) => {
      if (!isDrawing) return;

      const ctx = getCanvasContext();
      if (!ctx) return;

      ctx.lineTo(pos.x, pos.y);
      ctx.stroke();
    },
    [isDrawing]
  );

  const stopDrawing = useCallback(() => {
    setIsDrawing(false);
  }, []);

  // Mouse events
  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const pos = getMousePos(e);
    startDrawing(pos);
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const pos = getMousePos(e);
    draw(pos);
  };

  const handleMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    stopDrawing();
  };

  // Touch events
  const handleTouchStart = (e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const pos = getTouchPos(e);
    startDrawing(pos);
  };

  const handleTouchMove = (e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const pos = getTouchPos(e);
    draw(pos);
  };

  const handleTouchEnd = (e: React.TouchEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    stopDrawing();
  };

  const handleClearCanvas = () => {
    const canvas = canvasRef.current;
    const ctx = getCanvasContext();
    if (!canvas || !ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
  };

  // Convert data URL to blob for form data
  const dataURLToBlob = (dataURL: string): Blob => {
    const parts = dataURL.split(",");
    const contentType = parts[0].match(/:(.*?);/)?.[1] || "image/png";
    const raw = window.atob(parts[1]);
    const rawLength = raw.length;
    const uInt8Array = new Uint8Array(rawLength);

    for (let i = 0; i < rawLength; ++i) {
      uInt8Array[i] = raw.charCodeAt(i);
    }

    return new Blob([uInt8Array], { type: contentType });
  };

  const handleSave = async () => {
    if (!slideElement || !canvasRef.current || !slideDisplayRef.current) return;

    if (!prompt.trim()) {
      alert("Please enter a prompt before saving.");
      return;
    }

    setIsUpdating(true);

    try {
      // Take screenshot of the slide display area (slide only)
      const slideOnly = await html2canvas(slideDisplayRef.current, {
        backgroundColor: "#ffffff",
        scale: 1,
        logging: false,
        useCORS: true,
        ignoreElements: (element) => {
          // Ignore the canvas element when taking screenshot of slide only
          return element.tagName === "CANVAS";
        },
      });

      // Take screenshot of the entire slide display area including canvas
      const slideWithCanvas = await html2canvas(slideDisplayRef.current, {
        backgroundColor: "#ffffff",
        scale: 1,
        logging: false,
        useCORS: true,
      });

      // Get the current HTML content from the original slide element
      const currentHtml = slideElement.innerHTML;

      // Convert canvas images to blobs
      const currentUiImageBlob = dataURLToBlob(
        slideOnly.toDataURL("image/png")
      );
      const sketchImageBlob = dataURLToBlob(
        slideWithCanvas.toDataURL("image/png")
      );

      // Prepare form data
      const formData = new FormData();
      formData.append(
        "current_ui_image",
        currentUiImageBlob,
        `slide-${slideNumber}-current.png`
      );
      formData.append(
        "sketch_image",
        sketchImageBlob,
        `slide-${slideNumber}-sketch.png`
      );
      formData.append("html", currentHtml);
      formData.append("prompt", prompt);

      // Call the API
      const response = await fetch("/api/v1/ppt/html-edit/", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`API call failed: ${response.statusText}`);
      }

      const data = await response.json();

      // Update the slide with new data
      onSlideUpdate({
        slide_number: slideNumber,
        html: data.edited_html || currentHtml,
        processed: true,
        processing: false,
        error: undefined,
      });
      // Close the drawing canvas
      onClose();
    } catch (error) {
      console.error("Error updating slide:", error);
      alert(
        `Error updating slide: ${
          error instanceof Error ? error.message : "Unknown error"
        }`
      );
    } finally {
      setIsUpdating(false);
    }
  };

  const handleEraserModeChange = (isEraser: boolean) => {
    setEraserMode(isEraser);
  };

  const handleStrokeColorChange = (color: string) => {
    setStrokeColor(color);
    setEraserMode(false); // Switch back to draw mode when selecting color
  };

  const handleStrokeWidthChange = (width: number) => {
    setStrokeWidth(width);
  };

  const colors = [
    "#000000",
    "#FF0000",
    "#00FF00",
    "#0000FF",
    "#FFFF00",
    "#FF00FF",
    "#00FFFF",
    "#FFA500",
  ];

  const strokeWidths = [1, 3, 5, 8, 12];

  return (
    <div
      ref={containerRef}
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      <div className="relative bg-white rounded-lg shadow-xl max-w-[95vw] max-h-[95vh] overflow-hidden flex flex-col">
        {/* Controls */}
        <div className="flex items-center justify-between p-4 border-b bg-gray-50 flex-shrink-0">
          <div className="flex items-center gap-4 flex-wrap">
            <h3 className="text-lg font-semibold">Edit Slide {slideNumber}</h3>

            {/* Drawing Tools */}
            <div className="flex items-center gap-2">
              <Button
                variant={!eraserMode ? "default" : "outline"}
                size="sm"
                onClick={() => handleEraserModeChange(false)}
                className="flex items-center gap-1"
              >
                <Pencil size={16} />
                Draw
              </Button>

              <Button
                variant={eraserMode ? "default" : "outline"}
                size="sm"
                onClick={() => handleEraserModeChange(true)}
                className="flex items-center gap-1"
              >
                <Eraser size={16} />
                Erase
              </Button>
            </div>

            {/* Color Picker */}
            {!eraserMode && (
              <div className="flex items-center gap-1">
                {colors.map((color) => (
                  <button
                    key={color}
                    className={`w-6 h-6 rounded-full border-2 ${
                      strokeColor === color
                        ? "border-gray-800"
                        : "border-gray-300"
                    }`}
                    style={{ backgroundColor: color }}
                    onClick={() => handleStrokeColorChange(color)}
                  />
                ))}
              </div>
            )}

            {/* Stroke Width */}
            <div className="flex items-center gap-1">
              {strokeWidths.map((width) => (
                <button
                  key={width}
                  className={`w-8 h-8 rounded border flex items-center justify-center ${
                    strokeWidth === width
                      ? "bg-blue-100 border-blue-500"
                      : "border-gray-300"
                  }`}
                  onClick={() => handleStrokeWidthChange(width)}
                >
                  <div
                    className="rounded-full bg-gray-800"
                    style={{
                      width: `${width + 2}px`,
                      height: `${width + 2}px`,
                    }}
                  />
                </button>
              ))}
            </div>

            <Button
              variant="outline"
              size="sm"
              onClick={handleClearCanvas}
              className="flex items-center gap-1"
            >
              <RotateCcw size={16} />
              Clear
            </Button>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <Button
              variant="outline"
              onClick={onClose}
              className="flex items-center gap-1"
            >
              <X size={16} />
              Close
            </Button>
          </div>
        </div>

        {/* Prompt Section */}
        <div className="p-4 border-b bg-gray-50 flex-shrink-0">
          <div className="space-y-2">
            <label
              htmlFor="edit-prompt"
              className="text-sm font-medium text-gray-700"
            >
              Describe the changes you want to make:
            </label>
            <div className="flex gap-2">
              <Textarea
                id="edit-prompt"
                placeholder="Enter your prompt here... (e.g., 'Change the title color to blue', 'Add a border to the image', etc.)"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                className="flex-1 min-h-[80px] max-h-[80px] resize-none"
                disabled={isUpdating}
              />
              <Button
                onClick={handleSave}
                disabled={isUpdating || !prompt.trim()}
                className="flex items-center gap-1 bg-green-600 hover:bg-green-700 px-6"
              >
                {isUpdating ? (
                  "Updating..."
                ) : (
                  <>
                    <SendHorizontal size={16} />
                    Update Slide
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>

        {/* Canvas Area */}
        <div className="flex-1 overflow-auto bg-gray-100 p-4">
          <div
            ref={slideDisplayRef}
            className="relative mx-auto bg-white shadow-lg"
            style={{
              width: canvasDimensions.width,
              height: canvasDimensions.height,
            }}
          >
            {/* Slide Background - Static HTML content */}
            <div
              ref={slideContentRef}
              className="absolute inset-0 z-10"
              style={{
                overflow: "hidden",
                isolation: "isolate",
                contain: "layout style paint",
              }}
              dangerouslySetInnerHTML={{
                __html: slideHtml,
              }}
            />

            {/* Drawing Canvas */}
            <canvas
              ref={canvasRef}
              width={canvasDimensions.width}
              height={canvasDimensions.height}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                zIndex: 20,
                cursor: eraserMode ? "grab" : "crosshair",
                pointerEvents: "auto",
                touchAction: "none",
              }}
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onMouseLeave={handleMouseUp}
              onTouchStart={handleTouchStart}
              onTouchMove={handleTouchMove}
              onTouchEnd={handleTouchEnd}
              onContextMenu={(e) => e.preventDefault()}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default DrawingCanvas;
