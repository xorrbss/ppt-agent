import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  AlertCircle,
  CheckCircle,
  Edit,
  Loader2,
  Pencil,
  Eraser,
  RotateCcw,
  SendHorizontal,
  X,
  Repeat2,
} from "lucide-react";
import React, { useState, useEffect, useRef, useCallback } from "react";
import ToolTip from "@/components/ToolTip";
import html2canvas from "html2canvas";
import SlideContent from "./SlideContent";

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
  const [isEditMode, setIsEditMode] = useState(false);
  const slideContentRef = useRef<HTMLDivElement>(null);

  // Drawing canvas states
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const slideDisplayRef = useRef<HTMLDivElement>(null);
  const [strokeWidth, setStrokeWidth] = useState(3);
  const [strokeColor, setStrokeColor] = useState("#000000");
  const [eraserMode, setEraserMode] = useState(false);
  const [isDrawing, setIsDrawing] = useState(false);
  const [slideHtml, setSlideHtml] = useState("");
  const [canvasDimensions, setCanvasDimensions] = useState({
    width: 1280,
    height: 720,
  });
  const [didYourDraw, setDidYourDraw] = useState(false);

  // Load Tailwind CSS dynamically for slide content
  useEffect(() => {
    if (slide.processed && slide.html) {
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

  // Set up canvas when entering edit mode
  useEffect(() => {
    if (isEditMode && slideContentRef.current && slide.html) {
      const rect = slideContentRef.current.getBoundingClientRect();

      setCanvasDimensions({
        width: Math.max(rect.width, 800),
        height: Math.max(rect.height, 600),
      });

      setSlideHtml(slide.html);
    }
  }, [isEditMode, slide.html]);

  // Apply optimizations once after slide content is rendered in edit mode
  useEffect(() => {
    if (isEditMode && slideDisplayRef.current && slideHtml) {
      const slideContent = slideDisplayRef.current;

      slideContent.style.pointerEvents = "none";
      slideContent.style.userSelect = "none";
      slideContent.style.transform = "translateZ(0)";
      slideContent.style.willChange = "auto";
      slideContent.style.backfaceVisibility = "hidden";

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

        el.onclick = null;
        el.onmousedown = null;
        el.onmouseup = null;
        el.onmousemove = null;
      });
    }
  }, [isEditMode, slideHtml]);

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
      setDidYourDraw(true);

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
    setDidYourDraw(false);
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
    if (
      !slideContentRef.current ||
      !canvasRef.current ||
      !slideDisplayRef.current
    )
      return;

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
          return element.tagName === "CANVAS";
        },
      });
      let slideWithCanvas;
      if (didYourDraw) {
        // Take screenshot of the entire slide display area including canvas
        slideWithCanvas = await html2canvas(slideDisplayRef.current, {
          backgroundColor: "#ffffff",
          scale: 1,
          logging: false,
          useCORS: true,
        });
      }

      const currentHtml = slide.html;

      const currentUiImageBlob = dataURLToBlob(
        slideOnly.toDataURL("image/png")
      );
      let sketchImageBlob;
      if (didYourDraw && slideWithCanvas) {
        sketchImageBlob = dataURLToBlob(slideWithCanvas.toDataURL("image/png"));
      }

      const formData = new FormData();
      formData.append(
        "current_ui_image",
        currentUiImageBlob,
        `slide-${slide.slide_number}-current.png`
      );
      if (didYourDraw && slideWithCanvas && sketchImageBlob) {
        formData.append(
          "sketch_image",
          sketchImageBlob,
          `slide-${slide.slide_number}-sketch.png`
        );
      }
      formData.append("html", currentHtml);
      formData.append("prompt", prompt);

      const response = await fetch("/api/v1/ppt/html-edit/", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`API call failed: ${response.statusText}`);
      }

      const data = await response.json();

      const updatedSlideData = {
        slide_number: slide.slide_number,
        html: data.edited_html || currentHtml,
        processed: true,
        processing: false,
        error: undefined,
      };

      if (onSlideUpdate) {
        onSlideUpdate(updatedSlideData);
      } else {
        setSlides((prevSlides) =>
          prevSlides.map((s, i) =>
            i === index ? { ...s, ...updatedSlideData } : s
          )
        );
      }

      // Exit edit mode
      setIsEditMode(false);
      setPrompt("");
      handleClearCanvas();
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

  const handleEditClick = () => {
    setIsEditMode(true);
  };

  const handleCancelEdit = () => {
    setIsEditMode(false);
    setPrompt("");
    handleClearCanvas();
  };

  const handleEraserModeChange = (isEraser: boolean) => {
    setEraserMode(isEraser);
  };

  const handleStrokeColorChange = (color: string) => {
    setStrokeColor(color);
    setEraserMode(false);
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
    <Card
      key={slide.slide_number}
      className="border-2 font-instrument_sans w-full relative"
    >
      <CardHeader className=" max-w-[1280px] mx-auto px-0 py-6">
        <CardTitle className="text-xl flex items-center justify-between">
          <div className="flex items-center w-full justify-between gap-2">
            <div>
              {slide.processing ? (
                <Loader2 className="w-6 h-6 text-blue-600 animate-spin" />
              ) : slide.processed ? (
                <CheckCircle className="w-6 h-6 text-green-600" />
              ) : slide.error ? (
                <AlertCircle className="w-6 h-6 text-red-600" />
              ) : (
                <div className="w-6 h-6 border-2 border-gray-300 rounded-full" />
              )}
            </div>

            <div className="flex  gap-6">
              {slide.processed && slide.html && !isEditMode && (
                <div className=" ">
                  <ToolTip content="Edit slide">
                    <button
                      onClick={handleEditClick}
                      className={`px-6 py-2 flex gap-2 text-sm items-center group-hover:scale-105 rounded-lg bg-[#5141e5] hover:shadow-md transition-all duration-300 cursor-pointer shadow-md `}
                    >
                      <Edit className="w-4 sm:w-5 h-4 sm:h-5 text-white" />
                      <span className="text-white">Edit Slide</span>
                    </button>
                  </ToolTip>
                </div>
              )}
              <div>
                <ToolTip content="Retry fetch">
                  <button
                    onClick={() => retrySlide(index)}
                    disabled={slide.processing}
                    className="px-6 py-2 flex gap-2 text-sm items-center group-hover:scale-105 rounded-lg bg-[#5141e5] hover:shadow-md transition-all duration-300 cursor-pointer shadow-md"
                  >
                    <Repeat2 className="w-4 sm:w-5 h-4 sm:h-5 text-white" />
                    <span className="text-white">Retry Fetch</span>
                  </button>
                </ToolTip>
              </div>
            </div>
          </div>
        </CardTitle>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Edit Mode Controls */}
        {isEditMode && slide.processed && slide.html && (
          <div className="border-2 max-w-[1280px] mx-auto border-blue-200 rounded-lg p-4 bg-blue-50 space-y-4">
            {/* Drawing Tools */}
            <div className="flex items-center justify-between flex-wrap gap-4">
              <div className="flex items-center gap-4 flex-wrap">
                {/* Drawing Tools */}
                <div className="flex items-center gap-2">
                  <Button
                    variant={!eraserMode ? "default" : "outline"}
                    size="sm"
                    onClick={() => handleEraserModeChange(false)}
                    className="flex items-center gap-1"
                  >
                    <Pencil size={14} />
                    Draw
                  </Button>

                  <Button
                    variant={eraserMode ? "default" : "outline"}
                    size="sm"
                    onClick={() => handleEraserModeChange(true)}
                    className="flex items-center gap-1"
                  >
                    <Eraser size={14} />
                    Erase
                  </Button>
                </div>

                {/* Color Picker */}
                {!eraserMode && (
                  <div className="flex items-center gap-1">
                    {colors.map((color) => (
                      <button
                        key={color}
                        className={`w-5 h-5 rounded-full border-2 ${
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
                      className={`w-7 h-7 rounded border flex items-center justify-center ${
                        strokeWidth === width
                          ? "bg-blue-100 border-blue-500"
                          : "border-gray-300"
                      }`}
                      onClick={() => handleStrokeWidthChange(width)}
                    >
                      <div
                        className="rounded-full bg-gray-800"
                        style={{
                          width: `${width + 1}px`,
                          height: `${width + 1}px`,
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
                  <RotateCcw size={14} />
                  Clear
                </Button>
              </div>

              <Button
                variant="outline"
                size="sm"
                onClick={handleCancelEdit}
                className="flex items-center gap-1"
              >
                <X size={14} />
                Cancel
              </Button>
            </div>

            {/* Prompt Section */}
            <div className="space-y-2 mt-2">
              <label
                htmlFor="edit-prompt"
                className="text-sm font-medium font-inter text-gray-700"
              >
                Describe the changes you want to make:
              </label>
              <div className="flex gap-2">
                <Textarea
                  id="edit-prompt"
                  placeholder="Enter your prompt here... (e.g., 'Change the title color to blue', 'Add a border to the image', etc.)"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  className="flex-1 font-inter duration-300 h-[70px] border-blue-200 border-2 rounded-lg outline-none focus:border-blue-500 focus:ring-0 max-h-[70px] resize-none "
                  disabled={isUpdating}
                />
                <div>
                  <Button
                    onClick={handleSave}
                    disabled={isUpdating || !prompt.trim()}
                    className="flex flex-col w-28 font-inter font-semibold items-center gap-1 h-full bg-green-600 hover:bg-green-700 px-4"
                  >
                    {isUpdating ? (
                      "Updating..."
                    ) : (
                      <>
                        <SendHorizontal size={14} />
                        Update
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}

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
          <div className="relative">
            <div ref={slideDisplayRef} className="relative mx-auto w-full ">
              {/* <div
                ref={slideContentRef}
                className="relative"
                dangerouslySetInnerHTML={{
                  __html: slide.html,
                }}
              /> */}
              <div ref={slideContentRef}>
                <SlideContent slide={slide} />
              </div>
              {isEditMode && (
                <canvas
                  ref={canvasRef}
                  width={canvasDimensions.width}
                  height={canvasDimensions.height}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    zIndex: 30,
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
              )}
            </div>
          </div>
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

      {/* Action Buttons */}
      <div className="p-4 pt-0 flex gap-2">
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
      </div>
    </Card>
  );
};

export default EachSlide;
