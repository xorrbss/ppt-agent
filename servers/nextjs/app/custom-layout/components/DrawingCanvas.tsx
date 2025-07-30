"use client";

import React, { useRef, useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Save, X, Pencil, Eraser, RotateCcw, Download } from "lucide-react";
import html2canvas from "html2canvas";

interface DrawingCanvasProps {
  slideElement: HTMLElement | null;
  onClose: () => void;
  slideNumber: number;
}

const DrawingCanvas: React.FC<DrawingCanvasProps> = ({
  slideElement,
  onClose,
  slideNumber,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const slideDisplayRef = useRef<HTMLDivElement>(null);
  const [strokeWidth, setStrokeWidth] = useState(3);
  const [strokeColor, setStrokeColor] = useState("#000000");
  const [eraserMode, setEraserMode] = useState(false);
  const [isDrawing, setIsDrawing] = useState(false);
  const [canvasDimensions, setCanvasDimensions] = useState({
    width: 800,
    height: 600,
  });

  useEffect(() => {
    if (slideElement && containerRef.current) {
      const rect = slideElement.getBoundingClientRect();

      // Set canvas dimensions to match the slide element
      setCanvasDimensions({
        width: Math.max(rect.width, 800),
        height: Math.max(rect.height, 600),
      });
    }
  }, [slideElement]);

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

  const downloadImage = (dataURL: string, filename: string) => {
    const link = document.createElement("a");
    link.download = filename;
    link.href = dataURL;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleSave = async () => {
    if (!slideElement || !canvasRef.current || !slideDisplayRef.current) return;

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

      // Download both images
      const slideOnlyDataURL = slideOnly.toDataURL("image/png");
      const slideWithCanvasDataURL = slideWithCanvas.toDataURL("image/png");

      downloadImage(slideOnlyDataURL, `slide-${slideNumber}-original.png`);
      downloadImage(
        slideWithCanvasDataURL,
        `slide-${slideNumber}-with-annotations.png`
      );
    } catch (error) {
      console.error("Error capturing slide:", error);
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
              onClick={handleSave}
              className="flex items-center gap-1 bg-green-600 hover:bg-green-700"
            >
              <Download size={16} />
              Save & Download
            </Button>
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
            {/* Slide Background */}
            {slideElement && (
              <div
                className="absolute inset-0 pointer-events-none z-10"
                dangerouslySetInnerHTML={{
                  __html: slideElement.innerHTML,
                }}
              />
            )}

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
              }}
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onMouseLeave={handleMouseUp}
              onTouchStart={handleTouchStart}
              onTouchMove={handleTouchMove}
              onTouchEnd={handleTouchEnd}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default DrawingCanvas;
