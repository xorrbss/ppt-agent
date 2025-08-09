"use client";

import React, { useEffect, useState, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
// import { useGroupLayoutLoader } from '../hooks/useGroupLayoutLoader'
import LoadingStates from "../components/LoadingStates";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Edit, Home, Trash2 } from "lucide-react";
import { useLayout } from "@/app/(presentation-generator)/context/LayoutContext";

import html2canvas from "html2canvas";

const GroupLayoutPreview = () => {
  const params = useParams();
  const router = useRouter();
  const slug = params.slug as string;
  // const isCustom = slug.includes("custom-");
  const isCustom = true;
  // Custom hooks
  const {
    canvasRef,
    slideDisplayRef,
    strokeWidth,
    strokeColor,
    eraserMode,
    isDrawing,
    canvasDimensions,
    setCanvasDimensions,
    didYourDraw,
    handleMouseDown,
    handleMouseMove,
    handleMouseUp,
    handleTouchStart,
    handleTouchMove,
    handleTouchEnd,
    handleClearCanvas,
    handleEraserModeChange,
    handleStrokeColorChange,
    handleStrokeWidthChange,
  } = useDrawingCanvas();

  const slideContentRef = useRef<HTMLDivElement | null>(null);

  const { getFullDataByGroup, loading,refetch } = useLayout();
  const layoutGroup = getFullDataByGroup(slug);
  const [isEditMode, setIsEditMode] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [prompt, setPrompt] = useState("");
  const [isUpdating, setIsUpdating] = useState(false);

  useEffect(() => {
    const existingScript = document.querySelector(
      'script[src*="tailwindcss.com"]'
    );
    if (!existingScript) {
      const script = document.createElement("script");
      script.src = "https://cdn.tailwindcss.com";
      script.async = true;
      document.head.appendChild(script);
    }
  }, [slug]);

  // Size canvas to content when entering edit mode
  useEffect(() => {
    if (isEditMode && slideContentRef.current) {
      const rect = slideContentRef.current.getBoundingClientRect();
      setCanvasDimensions({
        width: Math.max(rect.width, 800),
        height: Math.max(rect.height, 600),
      });
    }
  }, [isEditMode, setCanvasDimensions]);

  // Handle loading state
  if (loading) {
    return <LoadingStates type="loading" />;
  }

  // Handle empty state
  if (!layoutGroup || layoutGroup.length === 0) {
    return <LoadingStates type="empty" />;
  }
  const deleteLayouts = async () => {
    const presentationId = slug.replace('custom-','');
    refetch();
    router.back();
    const response = await fetch(`/api/v1/ppt/layout-management/delete-layouts/${presentationId}`, {
      method: "DELETE",
    }); 
    if (response.ok) {
      router.push("/template-preview");
    }
  }

 const handleSave = async (
    slideDisplayRef: React.RefObject<HTMLDivElement |null>,
    didYourDraw: boolean
  ) => {
    if (
      !slideContentRef.current ||
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

      

      const currentUiImageBlob = dataURLToBlob(
        slideOnly.toDataURL("image/png")
      );
      let sketchImageBlob;
      if (didYourDraw && slideWithCanvas) {
        sketchImageBlob = dataURLToBlob(slideWithCanvas.toDataURL("image/png"));
      }

      // download the images

      const currentUiImageUrl = URL.createObjectURL(currentUiImageBlob);
      const sketchImageUrl = didYourDraw ? URL.createObjectURL(sketchImageBlob) : null;

      const a = document.createElement("a");
      a.href = currentUiImageUrl;
      a.download = `slide-current.png`;
      a.click();
      
      if (sketchImageUrl) {
        const b = document.createElement("a");
        b.href = sketchImageUrl;
        b.download = `slide-sketch.png`;
        b.click();
      }


      // const formData = new FormData();
      // formData.append(
      //   "current_ui_image",
      //   currentUiImageBlob,
      //   `slide--current.png`
      // );
      // if (didYourDraw && slideWithCanvas && sketchImageBlob) {
      //   formData.append(
      //     "sketch_image",
      //     sketchImageBlob,
      //     `slide-sketch.png`
      //   );
      // }
      // formData.append("html", '');
      // formData.append("prompt", prompt);

      // const response = await fetch("/api/v1/ppt/html-edit/", {
      //   method: "POST",
      //   body: formData,
      // });

      // if (!response.ok) {
      //   throw new Error(`API call failed: ${response.statusText}`);
      // }

      // const data = await response.json();


      // Exit edit mode
      setIsEditMode(false);
      setPrompt("");
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

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b sticky top-0 z-30">
        <div className="max-w-7xl mx-auto px-6 py-6">
          {/* Navigation */}
          <div className="flex items-center gap-4 mb-4">
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.back()}
              className="flex items-center gap-2"
            >
              <ArrowLeft className="w-4 h-4" />
              Back
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.push("/template-preview")}
              className="flex items-center gap-2"
            >
              <Home className="w-4 h-4" />
              All Groups
            </Button>
             {isCustom && <button className=" border border-red-200 flex justify-center items-center gap-2 text-red-700 px-4 py-1 rounded-md" onClick={() => {
            deleteLayouts();
          }}><Trash2 className="w-4 h-4" />Delete</button>}
          </div>

          <div className="text-center">
            <h1 className="text-3xl font-bold text-gray-900 capitalize">
              {layoutGroup[0].groupName} Layouts
            </h1>
            <p className="text-gray-600 mt-2">
              {layoutGroup.length} layout{layoutGroup.length !== 1 ? "s" : ""} •{" "}
              {layoutGroup[0].groupName}
            </p>
          </div>
         
        </div>
      </header>

     
      <main className="max-w-7xl mx-auto px-6 py-8">
        {/* Edit Controls (no HTML editor) */}
        {isCustom && (
          <EditControls
            isEditMode={isEditMode}
            prompt={prompt}
            isUpdating={isUpdating}
            strokeWidth={strokeWidth}
            strokeColor={strokeColor}
            eraserMode={eraserMode}
            onPromptChange={setPrompt}
            onSave={() => {
              setIsUpdating(true);
              setTimeout(() => {
                setIsUpdating(false);
                setIsEditMode(false);
                setSelectedIndex(null);
              }, 300);
            }}
            onCancel={() => {
              setIsEditMode(false);
              setSelectedIndex(null);
              handleClearCanvas();
            }}
            onStrokeWidthChange={handleStrokeWidthChange}
            onStrokeColorChange={handleStrokeColorChange}
            onEraserModeChange={handleEraserModeChange}
            onClearCanvas={handleClearCanvas}
          />
        )}
        <div className="space-y-8">
          {layoutGroup.map((layout: any, index: number) => {
            const {
              component: LayoutComponent,
              sampleData,
              name,
              fileName,
            } = layout;

            const isSelected = isCustom && isEditMode && selectedIndex === index;

            return (
              <Card
                key={`${layoutGroup[0].groupName}-${index}`}
                className="overflow-hidden shadow-md hover:shadow-lg transition-shadow"
              >
                {/* Layout Header */}
                <div className="bg-white px-6 py-4 border-b">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-xl font-semibold text-gray-900">
                        {name}
                      </h3>
                      <div className="flex items-center gap-4 mt-1">
                        <span className="text-sm text-gray-500 font-mono">
                          {fileName}
                        </span>
                        <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                          {layoutGroup[0].groupName}
                        </span>
                      </div>
                    </div>
                    <div className="text-right">
                      {isCustom && (
                        <button
                          className="border flex items-center gap-2 border-blue-400 bg-blue-50 px-4 py-1 rounded-md text-blue-700"
                          onClick={() => {
                            setIsEditMode(true);
                            setSelectedIndex(index);
                          }}
                        >
                          <Edit className="w-4 h-4" />Edit
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                {/* Layout Content */}
                <div ref={isSelected ? slideDisplayRef : undefined} className="relative mx-auto w-full">
                  <div 
                    ref={isSelected ? slideContentRef : undefined}
                    className="bg-gray-50 aspect-video max-w-[1280px] w-full"
                  >
                    <LayoutComponent data={sampleData} />
                    {isSelected && (
                      <canvas
                        ref={canvasRef!}
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
              </Card>
            );
          })}
        </div>
      </main>

      {/* Footer */}
      <footer className="bg-white border-t mt-16">
        <div className="max-w-7xl mx-auto px-6 py-8">
          <div className="text-center text-gray-600">
            <p>
              {layoutGroup[0].groupName} • {layoutGroup.length} components
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default GroupLayoutPreview;
