"use client";
import React, { useEffect, useState, useRef } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Wand2,
  Upload,
  Move,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useSelector } from "react-redux";
import { PresentationGenerationApi } from "../services/api/presentation-generation";
import { RootState } from "@/store/store";
import { useSearchParams } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";
import { ThemeImagePrompt } from "../utils/others";

interface ImageEditorProps {
  initialImage: string | null;
  imageIdx?: number;
  slideIndex: number;
  className?: string;
  promptContent?: string;
  properties?: null | any;
  onClose?: () => void;
  onImageChange?: (newImageUrl: string, prompt?: string) => void;

}

const ImageEditor = ({
  initialImage,
  imageIdx = 0,
  slideIndex,
  promptContent,
  properties,
  onClose,
  onImageChange,

}: ImageEditorProps) => {

  // State management
  const [image, setImage] = useState(initialImage);
  const [previewImages, setPreviewImages] = useState([initialImage]);
  const [prompt, setPrompt] = useState<string>("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadedImageUrl, setUploadedImageUrl] = useState<string | null>(null);

  // Focus point and object fit for image editing
  const [isFocusPointMode, setIsFocusPointMode] = useState(false);
  const [focusPoint, setFocusPoint] = useState(
    (properties &&
      properties[imageIdx] &&
      properties[imageIdx].initialFocusPoint) || {
      x: 50,
      y: 50,
    }
  );
  const [objectFit, setObjectFit] = useState<"cover" | "contain" | "fill">(
    (properties &&
      properties[imageIdx] &&
      properties[imageIdx].initialObjectFit) ||
    "cover"
  );

  // Refs
  const imageRef = useRef<HTMLImageElement>(null);
  const imageContainerRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const popoverContentRef = useRef<HTMLDivElement>(null);

  // Update local state when initial image changes
  useEffect(() => {
    setImage(initialImage);
    setPreviewImages([initialImage]);
  }, [initialImage]);

  // Close toolbar when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        imageContainerRef.current &&
        !imageContainerRef.current.contains(event.target as Node) &&
        toolbarRef.current &&
        !toolbarRef.current.contains(event.target as Node) &&
        !popoverContentRef.current
      ) {
        if (isFocusPointMode) {
          saveImageProperties(objectFit, focusPoint);
        }
        setIsFocusPointMode(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isFocusPointMode, focusPoint, objectFit]);

  /**
   * Handles image selection and calls the parent callback
   */
  const handleImageChange = (newImage: string) => {
    setImage(newImage);

    if (onImageChange) {
      onImageChange(newImage, promptContent);
    }
  };

  /**
   * Handles focus point adjustment when clicking on the image
   */
  const handleFocusPointClick = (e: React.MouseEvent) => {
    if (!isFocusPointMode || !imageRef.current) return;

    const rect = imageRef.current.getBoundingClientRect();
    const x = Math.max(
      0,
      Math.min(100, ((e.clientX - rect.left) / rect.width) * 100)
    );
    const y = Math.max(
      0,
      Math.min(100, ((e.clientY - rect.top) / rect.height) * 100)
    );

    setFocusPoint({ x, y });
    saveImageProperties(objectFit, { x, y });

    // Apply the focus point in real-time
    if (imageRef.current) {
      imageRef.current.style.objectPosition = `${x}% ${y}%`;
    }
  };

  /**
   * Toggles focus point adjustment mode
   */
  const toggleFocusPointMode = () => {
    if (isFocusPointMode) {
      saveImageProperties(objectFit, focusPoint);
    }
    setIsFocusPointMode(!isFocusPointMode);
  };

  /**
   * Handles object fit change
   */
  const handleFitChange = (fit: "cover" | "contain" | "fill") => {
    setObjectFit(fit);

    if (imageRef.current) {
      imageRef.current.style.objectFit = fit;
    }

    saveImageProperties(fit, focusPoint);
  };

  /**
   * Saves image properties (focus point and object fit)
   */
  const saveImageProperties = (
    fit: "cover" | "contain" | "fill",
    focusPoint: { x: number; y: number }
  ) => {
    const propertiesData = {
      initialObjectFit: fit,
      initialFocusPoint: focusPoint,
    };
    // TODO: Save to Redux store if needed
  };

  /**
   * Generates new images using AI
   */
  const handleGenerateImage = async () => {
    if (!prompt) {
      setError("Please enter a prompt");
      return;
    }
    console.log("prompt", prompt);
    try {
      setIsGenerating(true);
      setError(null);
      const response = await PresentationGenerationApi.generateImage({
        prompt: prompt,
      });

      setPreviewImages(response.paths);
    } catch (err) {
      console.error("Error in image generation", err);
      setError("Failed to generate image. Please try again.");
    } finally {
      setIsGenerating(false);
    }
  };

  /**
   * Handles file upload
   */
  const handleFileUpload = async (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const file = event.target.files?.[0];
    if (!file) return;

    // Validate file size (5MB limit)
    if (file.size > 5 * 1024 * 1024) {
      setUploadError("File size should be less than 5MB");
      return;
    }

    // Validate file type
    if (!file.type.startsWith("image/")) {
      setUploadError("Please upload an image file");
      return;
    }

    try {
      setIsUploading(true);
      setUploadError(null);

      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch('/api/upload-image', {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.error || 'Upload failed');
      }

      setUploadedImageUrl(result.filePath);
    } catch (err) {
      setUploadError("Failed to upload image. Please try again.");
      console.error("Upload error:", err);
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="image-editor-container">


      <Sheet open={true} onOpenChange={() => onClose?.()}>
        <SheetContent
          side="right"
          className="w-[600px]"
          onOpenAutoFocus={(e) => e.preventDefault()}
          onClick={(e) => e.stopPropagation()}
        >
          <SheetHeader>
            <SheetTitle>Update Image</SheetTitle>
          </SheetHeader>

          <div className="mt-6">
            <Tabs defaultValue="generate" className="w-full">
              <TabsList className="grid bg-blue-100 border border-blue-300 w-full grid-cols-2 mx-auto">
                <TabsTrigger className="font-medium" value="generate">
                  AI Generate
                </TabsTrigger>
                <TabsTrigger className="font-medium" value="upload">
                  Upload
                </TabsTrigger>
              </TabsList>
              {/* Generate Tab */}
              <TabsContent value="generate" className="mt-4 space-y-4">
                <div className="space-y-4">
                  <div>
                    <h3 className="text-sm font-medium mb-1">Current Prompt</h3>
                    <p className="text-sm text-gray-500">{promptContent}</p>
                  </div>

                  <div>
                    <h3 className="text-base font-medium mb-2">Image Description</h3>
                    <Textarea
                      placeholder="Describe the image you want to generate..."
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                      className="min-h-[100px]"
                    />
                  </div>

                  <Button
                    onClick={handleGenerateImage}
                    className="w-full"
                    disabled={!prompt || isGenerating}
                  >
                    <Wand2 className="w-4 h-4 mr-2" />
                    {isGenerating ? "Generating..." : "Generate Image"}
                  </Button>

                  {error && <p className="text-red-500 text-sm">{error}</p>}

                  <div className="grid grid-cols-2 gap-4">
                    {isGenerating || previewImages.length === 0
                      ? Array.from({ length: 4 }).map((_, index) => (
                        <Skeleton
                          key={index}
                          className="aspect-[4/3] w-full rounded-lg"
                        />
                      ))
                      : previewImages.map((image, index) => (
                        <div
                          key={index}
                          onClick={() => handleImageChange(image as string)}
                          className="aspect-[4/3] w-full overflow-hidden rounded-lg border cursor-pointer hover:border-blue-500 transition-colors"
                        >
                          {image && (
                            <img
                              src={image}
                              alt={`Preview ${index + 1}`}
                              className="w-full h-full object-cover"
                            />
                          )}
                        </div>
                      ))}
                  </div>
                </div>
              </TabsContent>

              {/* Upload Tab */}
              <TabsContent value="upload" className="mt-4 space-y-4">
                <div className="space-y-4">
                  <div
                    className={cn(
                      "border-2 border-dashed rounded-lg p-8 text-center transition-colors",
                      isUploading
                        ? "border-gray-400 bg-gray-50"
                        : "border-gray-300 hover:border-blue-400"
                    )}
                  >
                    <input
                      type="file"
                      id="file-upload"
                      className="hidden"
                      accept="image/*"
                      onChange={handleFileUpload}
                      disabled={isUploading}
                    />
                    <label
                      htmlFor="file-upload"
                      className={cn(
                        "flex flex-col items-center",
                        isUploading ? "cursor-wait" : "cursor-pointer"
                      )}
                    >
                      {isUploading ? (
                        <div className="w-8 h-8 border-2 border-gray-400 border-t-transparent rounded-full animate-spin mb-2" />
                      ) : (
                        <Upload className="w-8 h-8 text-gray-500 mb-2" />
                      )}
                      <span className="text-sm text-gray-600">
                        {isUploading
                          ? "Uploading your image..."
                          : "Click to upload an image"}
                      </span>
                      <span className="text-xs text-gray-500 mt-1">
                        Maximum file size: 5MB
                      </span>
                    </label>
                  </div>

                  {uploadError && (
                    <p className="text-red-500 text-sm text-center">
                      {uploadError}
                    </p>
                  )}

                  {(uploadedImageUrl || isUploading) && (
                    <div className="mt-4">
                      <h3 className="text-sm font-medium mb-2">
                        Uploaded Image Preview
                      </h3>
                      <div className="aspect-[4/3] relative rounded-lg overflow-hidden border border-gray-200">
                        {isUploading ? (
                          <div className="w-full h-full bg-gray-100 flex items-center justify-center">
                            <div className="flex flex-col items-center">
                              <div className="w-8 h-8 border-2 border-gray-400 border-t-transparent rounded-full animate-spin mb-2" />
                              <span className="text-sm text-gray-500">
                                Processing...
                              </span>
                            </div>
                          </div>
                        ) : (
                          uploadedImageUrl && (
                            <div
                              onClick={() =>
                                handleImageChange(uploadedImageUrl)
                              }
                              className="cursor-pointer group w-full h-full"
                            >
                              <img
                                src={uploadedImageUrl}
                                alt="Uploaded preview"
                                className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                              />
                              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-all duration-200" />
                              <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                                <span className="bg-white/90 px-3 py-1 rounded-full text-sm font-medium">
                                  Click to use this image
                                </span>
                              </div>
                            </div>
                          )
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </TabsContent>
            </Tabs>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
};

export default React.memo(ImageEditor);
