"use client";
/* eslint-disable @next/next/no-img-element -- Native img nodes are required for crop geometry and arbitrary blob/cross-origin editor sources. */
import React, { useCallback, useEffect, useState, useRef, useMemo } from "react";
import { useTranslations } from "next-intl";
import { useSelector } from "react-redux";
import { RootState } from "@/store/store";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Wand2, Upload, Loader2, Trash, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { PresentationGenerationApi } from "../services/api/presentation-generation";
import { Skeleton } from "@/components/ui/skeleton";
import { notify } from "@/components/ui/sonner";
import { PreviousGeneratedImagesResponse } from "../services/api/params";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { ImagesApi } from "../services/api/images";
import { ImageAssetResponse } from "../services/api/types";
import { useUploadLimits } from "@/lib/use-upload-limits";
import {
  getImageUploadValidationError,
  getStockImageApiKey,
  resolveEditorImageSource,
  resolveStockImageProvider,
} from "./imageEditorHelpers";
import {
  cropScaleFromProperties,
  imageCropDomStyle,
  imageCropProperties,
  normalizeImageFocusPoint,
  resolveImageEditorProperties,
  type ImageFit,
} from "@/components/slide-editor/images/imageCrop";

interface ImageEditorProps {
  initialImage: string | null;
  imageIdx?: number;
  slideIndex: number;
  className?: string;
  promptContent?: string;
  properties?: null | any;
  onClose?: () => void;
  onImageChange?: (newImageUrl: string, prompt?: string) => void;
  onFocusPointClick?: (propertiesData: any) => void;
}

const ImageEditor = ({
  initialImage,
  imageIdx = 0,
  promptContent,
  properties,
  onClose,
  onFocusPointClick,
  onImageChange,
}: ImageEditorProps) => {
  const t = useTranslations("imageEditor");
  const uploadLimits = useUploadLimits();
  const llmConfig = useSelector((state: RootState) => state.userConfig.llm_config);
  const stockImageProvider = useMemo(
    () => resolveStockImageProvider(llmConfig),
    [llmConfig]
  );

  // State management
  const [previewImages, setPreviewImages] = useState(
    resolveEditorImageSource(initialImage)
  );
  const [previousGeneratedImages, setPreviousGeneratedImages] = useState<
    PreviousGeneratedImagesResponse[]
  >([]);
  const [prompt, setPrompt] = useState<string>("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [stockSearchResults, setStockSearchResults] = useState<string[]>([]);
  const [isSearchingStock, setIsSearchingStock] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadedImageUrl, setUploadedImageUrl] = useState<string | null>(null);
  const [isOpen, setIsOpen] = useState(true);
  const [uploadedImages, setUploadedImages] = useState<ImageAssetResponse[]>([]);
  const [uploadedImagesLoading, setUploadedImagesLoading] = useState(false);
  const imageProperties = useMemo(
    () => resolveImageEditorProperties(properties, imageIdx),
    [imageIdx, properties]
  );
  // Focus point and object fit for image editing
  const [isFocusPointMode, setIsFocusPointMode] = useState(false);
  const [focusPoint, setFocusPoint] = useState(() =>
    normalizeImageFocusPoint(imageProperties.initialFocusPoint)
  );
  const [objectFit, setObjectFit] = useState<ImageFit>(
    imageProperties.initialObjectFit || "cover"
  );
  const [cropScale, setCropScale] = useState(() =>
    cropScaleFromProperties(imageProperties)
  );

  // Refs
  const imageRef = useRef<HTMLImageElement>(null);

  const getPreviousGeneratedImage = useCallback(async () => {
    try {
      trackEvent(MixpanelEvent.ImageEditor_GetPreviousGeneratedImages_API_Call);
      const response =
        await PresentationGenerationApi.getPreviousGeneratedImages();
      setPreviousGeneratedImages(response);
    } catch (error: any) {
      notify.error(t("toasts.loadTitle"), t("errors.previousImages"));
      console.error("error in getting previous generated images", error);
      setError(error.message || t("errors.previousImages"));
    }
  }, [t]);

  useEffect(() => {
    setPreviewImages(resolveEditorImageSource(initialImage));
  }, [initialImage]);

  useEffect(() => {
    setFocusPoint(normalizeImageFocusPoint(imageProperties.initialFocusPoint));
    setObjectFit(imageProperties.initialObjectFit || "cover");
    setCropScale(cropScaleFromProperties(imageProperties));
  }, [imageProperties]);

  useEffect(() => {
    setPrompt((prev) => (prev.trim() ? prev : promptContent || ""));
  }, [promptContent]);

  useEffect(() => {
    if (stockImageProvider) {
      setStockSearchResults([]);
    }
  }, [stockImageProvider]);

  useEffect(() => {
    if (isOpen && !previousGeneratedImages.length && !stockImageProvider) {
      getPreviousGeneratedImage();
    }
  }, [
    getPreviousGeneratedImage,
    isOpen,
    previousGeneratedImages.length,
    stockImageProvider,
  ]);

  // Handle close with animation
  const handleClose = () => {

    setIsOpen(false);
    // Delay the actual close to allow animation to complete
    setTimeout(() => {
      onClose?.();
    }, 300); // Match the Sheet animation duration
  };

  /**
   * Handles image selection and calls the parent callback
   */
  const handleImageChange = (newImage: string) => {
    if (onImageChange) {
      const promptForSlide = stockImageProvider
        ? (prompt.trim() || promptContent || "")
        : promptContent;
      const resetFocus = { x: 50, y: 50 };
      setFocusPoint(resetFocus);
      setObjectFit("cover");
      setCropScale(1);
      saveImageProperties("cover", resetFocus, 1);
      onImageChange(newImage, promptForSlide);
      setPreviewImages(newImage);
    }
  };

  /**
   * Handles focus point adjustment when clicking on the image
   */
  const handleFocusPointClick = (e: React.MouseEvent) => {
    if (!isFocusPointMode || !imageRef.current) return;

    // Use the fixed crop frame, not the transformed image bounds. When scale is
    // below 1 the image is smaller than the frame, so image-relative coordinates
    // would jump or clamp while clicking the intentional free space.
    const rect =
      imageRef.current.parentElement?.getBoundingClientRect() ??
      imageRef.current.getBoundingClientRect();
    const x = Math.max(
      0,
      Math.min(100, ((e.clientX - rect.left) / rect.width) * 100)
    );
    const y = Math.max(
      0,
      Math.min(100, ((e.clientY - rect.top) / rect.height) * 100)
    );

    applyFocusPoint({ x, y });
  };

  const applyFocusPoint = (nextFocusPoint: { x: number; y: number }) => {
    const normalized = {
      x: Math.max(0, Math.min(100, nextFocusPoint.x)),
      y: Math.max(0, Math.min(100, nextFocusPoint.y)),
    };
    setFocusPoint(normalized);
    saveImageProperties(objectFit, normalized, cropScale);
    if (imageRef.current) {
      imageRef.current.style.objectPosition = `${normalized.x}% ${normalized.y}%`;
    }
  };

  const handleFocusPointKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if ((event.key === "Enter" || event.key === " ") && !isFocusPointMode) {
      event.preventDefault();
      setIsFocusPointMode(true);
      return;
    }
    if (event.key === "Escape" && isFocusPointMode) {
      event.preventDefault();
      saveImageProperties(objectFit, focusPoint, cropScale);
      setIsFocusPointMode(false);
      return;
    }
    if (!isFocusPointMode) return;

    const step = event.shiftKey ? 10 : 2;
    const movement = {
      ArrowLeft: { x: -step, y: 0 },
      ArrowRight: { x: step, y: 0 },
      ArrowUp: { x: 0, y: -step },
      ArrowDown: { x: 0, y: step },
    }[event.key];
    if (!movement) return;

    event.preventDefault();
    applyFocusPoint({
      x: focusPoint.x + movement.x,
      y: focusPoint.y + movement.y,
    });
  };

  /**
   * Toggles focus point adjustment mode
   */
  const toggleFocusPointMode = () => {
    if (isFocusPointMode) {
      saveImageProperties(objectFit, focusPoint, cropScale);
    }
    setIsFocusPointMode(!isFocusPointMode);
  };

  /**
   * Handles object fit change
   */
  const handleFitChange = (fit: ImageFit) => {
    const nextCropScale = fit === "cover" ? cropScale : 1;
    setObjectFit(fit);
    setCropScale(nextCropScale);

    if (imageRef.current) {
      Object.assign(
        imageRef.current.style,
        imageCropDomStyle(fit, focusPoint, nextCropScale)
      );
    }

    saveImageProperties(fit, focusPoint, nextCropScale);
  };

  const handleCropScaleChange = (scale: number) => {
    setObjectFit("cover");
    setCropScale(scale);
    if (imageRef.current) {
      Object.assign(
        imageRef.current.style,
        imageCropDomStyle("cover", focusPoint, scale)
      );
    }
    saveImageProperties("cover", focusPoint, scale);
  };

  /**
   * Saves image properties (focus point and object fit)
   */
  const saveImageProperties = (
    fit: ImageFit,
    focusPoint: { x: number; y: number },
    scale: number
  ) => {
    onFocusPointClick?.(imageCropProperties(fit, focusPoint, scale));
  };

  /**
   * Stock image search (Pexels / Pixabay) — returns multiple URLs to pick from.
   */
  const handleStockImageSearch = async () => {
    if (!prompt.trim()) {
      setError(t("errors.searchKeywordRequired"));
      return;
    }
    if (!stockImageProvider) return;

    const apiKey = getStockImageApiKey(stockImageProvider, llmConfig);

    if (!apiKey) {
      setError(
        t("errors.stockApiKey", {
          provider: t(`providers.${stockImageProvider}`),
        })
      );
      return;
    }

    try {
      setIsSearchingStock(true);
      setError(null);
      const urls = await ImagesApi.searchStockImages(prompt.trim(), 20, {
        provider: stockImageProvider,
        apiKey,
      });
      setStockSearchResults(urls);
      if (urls.length === 0) {
        setError(t("errors.noStockResults"));
      }
    } catch (err: unknown) {
      console.error("Stock image search error", err);
      const message =
        err instanceof Error ? err.message : t("errors.stockSearch");
      setError(message);
      setStockSearchResults([]);
    } finally {
      setIsSearchingStock(false);
    }
  };

  /**
   * Generates new images using AI
   */
  const handleGenerateImage = async () => {
    if (!prompt) {
      setError(t("errors.promptRequired"));
      return;
    }
    if (stockImageProvider) {
      await handleStockImageSearch();
      return;
    }
    try {
      setIsGenerating(true);
      setError(null);
      trackEvent(MixpanelEvent.ImageEditor_GenerateImage_API_Call);
      const response = await PresentationGenerationApi.generateImage({
        prompt: prompt,
      });

      setPreviewImages(resolveEditorImageSource(response));
    } catch (err: any) {
      console.error("Error in image generation", err);
      setError(err.message || t("errors.generation"));
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

    const validationError = getImageUploadValidationError(file, uploadLimits.image);
    if (validationError === "tooLarge") {
      setUploadError(
        t("errors.fileTooLarge", { limit: uploadLimits.image.mb })
      );
      return;
    }
    if (validationError === "invalidType") {
      setUploadError(t("errors.fileType"));
      return;
    }
    try {
      setIsUploading(true);
      setUploadError(null);
      trackEvent(MixpanelEvent.ImageEditor_UploadImage_API_Call);
      const result = await ImagesApi.uploadImage(file);
      setUploadedImageUrl(resolveEditorImageSource(result));
    } catch (err:any) {
      setUploadError(t("errors.upload"));
      notify.error(t("toasts.uploadTitle"), err.message || t("errors.upload"));
      console.log("Upload error:", err.message);
    } finally {
      setIsUploading(false);
    }
  };

  const getUploadedImages = async () => {
    try {
      setUploadedImagesLoading(true);
      const result = await ImagesApi.getUploadedImages();
      setUploadedImages(result);
    } catch (err:any) {
      notify.error(t("toasts.loadTitle"), err.message || t("errors.uploadedImages"));
      console.log("Get uploaded images error:", err.message);
    } finally {
      setUploadedImagesLoading(false);
    }
  };
  const handleTabChange = (value: string) => {
    if (value === "upload") {
      getUploadedImages();
    }
  };

  const handleDeleteImage = async (image_id: string) => {
    try {
      await ImagesApi.deleteImage(image_id);
      setUploadedImages(uploadedImages.filter((image) => image.id !== image_id));
      notify.success(t("toasts.deletedTitle"), t("toasts.deletedDescription"));
    } catch (err:any) {
      notify.error(t("toasts.deleteErrorTitle"), err.message || t("errors.delete"));
    }
  };
  return (
    <div className="image-editor-container">
      <Sheet open={isOpen} onOpenChange={() => handleClose()}>
        <SheetContent
          side="right"
          aria-describedby="image-editor-description"
          className="w-[calc(100vw-16px)] max-w-[600px] sm:w-[600px] [&>button]:h-11 [&>button]:w-11"
          onClick={(e) => e.stopPropagation()}
        >
          <SheetHeader>
            <SheetTitle>{t("title")}</SheetTitle>
            <p id="image-editor-description" className="sr-only">
              {t("description")}
            </p>
          </SheetHeader>

          <div className="mt-6">
            <Tabs defaultValue="generate" className="w-full" onValueChange={handleTabChange}>
              <TabsList className="grid bg-blue-100 border border-blue-300 w-full grid-cols-3 mx-auto">
                <TabsTrigger className="font-medium" value="generate">
                  {stockImageProvider ? t("tabs.stock") : t("tabs.generate")}
                </TabsTrigger>
                <TabsTrigger className="font-medium" value="upload">
                  {t("tabs.upload")}
                </TabsTrigger>
                <TabsTrigger className="font-medium" value="edit">
                  {t("tabs.edit")}
                </TabsTrigger>
              </TabsList>
              {/* Generate Tab */}
              <TabsContent value="generate" className="mt-4 space-y-4 overflow-y-auto hide-scrollbar h-[85vh]">
                <div className="space-y-4">
                  <div>
                    <h3 className="text-sm font-medium mb-1">{t("currentPrompt")}</h3>
                    <p className="text-sm text-gray-500">{promptContent}</p>
                  </div>

                  <div>
                    <h3 className="text-base font-medium mb-2">
                      {stockImageProvider ? t("searchKeyword") : t("imageDescription")}
                    </h3>
                    <Textarea
                      placeholder={
                        stockImageProvider
                          ? t("stockPlaceholder")
                          : t("generationPlaceholder")
                      }
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                      className="min-h-[100px]"
                    />
                  </div>

                  <Button
                    onClick={handleGenerateImage}
                    className="w-full"
                    disabled={!prompt.trim() || isGenerating || isSearchingStock}
                  >
                    {stockImageProvider ? (
                      <Search className="w-4 h-4 mr-2" />
                    ) : (
                      <Wand2 className="w-4 h-4 mr-2" />
                    )}
                    {stockImageProvider
                      ? isSearchingStock
                        ? t("searching")
                        : t("searchStock")
                      : isGenerating
                        ? t("generating")
                        : t("generate")}
                  </Button>

                  {error && <p className="text-red-500 text-sm">{error}</p>}

                  {stockImageProvider ? (
                    <div className="space-y-3">
                      <h3 className="text-sm font-medium">{t("searchResults")}</h3>
                      <div className="grid grid-cols-2 gap-3">
                        {isSearchingStock
                          ? Array.from({ length: 8 }).map((_, index) => (
                              <Skeleton
                                key={index}
                                className="aspect-[4/3] w-full rounded-lg"
                              />
                            ))
                          : stockSearchResults.map((url, index) => (
                              <button
                                type="button"
                                key={url}
                                aria-label={t("useStockImage", { index: index + 1 })}
                                onClick={() => handleImageChange(url)}
                                className="aspect-[4/3] w-full overflow-hidden rounded-lg border border-gray-200 cursor-pointer hover:border-blue-500 transition-colors text-left p-0 bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
                              >
                                <img
                                  src={url}
                                  alt=""
                                  className="w-full h-full object-cover"
                                />
                              </button>
                            ))}
                      </div>
                      {!isSearchingStock && stockSearchResults.length === 0 && (
                        <p className="text-sm text-gray-500">
                          {t("runSearchForThumbnails", {
                            provider: t(`providers.${stockImageProvider}`),
                          })}
                        </p>
                      )}
                    </div>
                  ) : (
                    <>
                      <div className="grid grid-cols-2 gap-4">
                        {isGenerating || !previewImages ? (
                          Array.from({ length: 4 }).map((_, index) => (
                            <Skeleton
                              key={index}
                              className="aspect-[4/3] w-full rounded-lg"
                            />
                          ))
                        ) : (
                          <button
                            type="button"
                            aria-label={t("useGeneratedImage")}
                            onClick={() => handleImageChange(previewImages)}
                            className="aspect-[4/3] w-full overflow-hidden rounded-lg border cursor-pointer hover:border-blue-500 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
                          >
                            {previewImages && (
                              <img
                                src={previewImages}
                                alt={t("previewAlt")}
                                className="w-full h-full object-cover"
                              />
                            )}
                          </button>
                        )}
                      </div>
                      {previousGeneratedImages.length > 0 && (
                        <div className="mt-4">
                          <h3 className="text-sm font-medium mb-2">
                            {t("previousImages")}
                          </h3>
                          <div className="grid grid-cols-2 gap-4  ">
                            {previousGeneratedImages.map((image) => (
                              <button
                                type="button"
                                aria-label={t("usePreviousImage", {
                                  prompt: image.extras.prompt,
                                })}
                                onClick={() =>
                                  handleImageChange(resolveEditorImageSource(image))
                                }
                                key={image.id}
                                className="aspect-[4/3] w-full overflow-hidden rounded-lg border cursor-pointer hover:border-blue-500 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
                              >
                                <img
                                  src={resolveEditorImageSource(image)}
                                  alt={image.extras.prompt}
                                  className="w-full h-full object-cover"
                                />
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </TabsContent>

              {/* Upload Tab */}
              <TabsContent value="upload" className="mt-4 space-y-4">
                <div className="space-y-4">
                  <div
                    className={cn(
                        "border-2 border-dashed rounded-lg p-8 text-center transition-colors focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-600 focus-within:ring-offset-2",
                      isUploading
                        ? "border-gray-400 bg-gray-50"
                        : "border-gray-300 hover:border-blue-400"
                    )}
                  >
                    <input
                      type="file"
                      id="file-upload"
                      className="sr-only"
                      accept="image/png,image/jpeg,image/gif,image/webp"
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
                        {isUploading ? t("uploading") : t("clickToUpload")}
                      </span>
                      <span className="text-xs text-gray-500 mt-1">
                        {t("maxFileSize", { limit: uploadLimits.image.mb })}
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
                        {t("uploadedPreviewTitle")}
                      </h3>
                      <div className="aspect-[4/3] relative rounded-lg overflow-hidden border border-gray-200">
                        {isUploading ? (
                          <div className="w-full h-full bg-gray-100 flex items-center justify-center">
                            <div className="flex flex-col items-center">
                              <div className="w-8 h-8 border-2 border-gray-400 border-t-transparent rounded-full animate-spin mb-2" />
                              <span className="text-sm text-gray-500">
                                {t("processing")}
                              </span>
                            </div>
                          </div>
                        ) : (
                          uploadedImageUrl && (
                            <button
                              type="button"
                              aria-label={t("useUploadedImage")}
                              onClick={() =>
                                handleImageChange(uploadedImageUrl)
                              }
                              className="cursor-pointer group w-full h-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-600"
                            >
                              <img
                                src={uploadedImageUrl}
                                alt={t("uploadedPreviewAlt")}
                                className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                              />
                              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-all duration-200" />
                              <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                                <span className="bg-white/90 px-3 py-1 rounded-full text-sm font-medium">
                                  {t("useThisImage")}
                                </span>
                              </div>
                            </button>
                          )
                        )}
                      </div>
                    </div>
                  )}
                  <div>
                    <h3 className="text-sm font-medium mb-2">{t("uploadedImages")}</h3>
                    <div className="grid grid-cols-2 gap-4">
                      {uploadedImagesLoading ? (
                        <div className="flex items-center justify-center">
                          <Loader2 className="w-4 h-4 animate-spin" />
                        </div>
                      ) : (
                        uploadedImages.map((image) => (
                          <div
                            key={image.id}
                            className="group relative aspect-[4/3] overflow-hidden rounded-lg border border-gray-200"
                          >
                            <button
                              type="button"
                              aria-label={t("useUploadedImageById", { id: image.id })}
                              onClick={() =>
                                handleImageChange(resolveEditorImageSource(image))
                              }
                              className="h-full w-full cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-600"
                            >
                              <img
                                src={resolveEditorImageSource(image)}
                                alt={t("uploadedPreviewAlt")}
                                className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                              />
                              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-all duration-200" />
                              <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                                <span className="bg-white/90 px-3 py-1 rounded-full text-xs font-medium">
                                  {t("use")}
                                </span>
                              </div>
                            </button>
                            <button
                              type="button"
                              aria-label={t("deleteUploadedImage", { id: image.id })}
                              onClick={(event) => {
                                event.stopPropagation();
                                handleDeleteImage(image.id);
                              }}
                              className="absolute right-1 top-1 z-10 flex h-10 w-10 items-center justify-center rounded-full bg-white/90 text-red-600 opacity-100 shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-600 sm:opacity-0 sm:group-hover:opacity-100 sm:focus:opacity-100"
                            >
                              <Trash aria-hidden="true" className="h-4 w-4" />
                            </button>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              </TabsContent>
              <TabsContent value="edit" className="mt-4 space-y-4">
                <div className="space-y-4">
                  <h3 className="text-sm font-medium mb-2">{t("currentImage")}</h3>
                  <div
                    role="button"
                    tabIndex={0}
                    aria-label={
                      isFocusPointMode
                        ? t("focusPointPosition", {
                            horizontal: Math.round(focusPoint.x),
                            vertical: Math.round(focusPoint.y),
                          })
                        : t("focusPoint")
                    }
                    aria-pressed={isFocusPointMode}
                    aria-keyshortcuts="Enter Space ArrowUp ArrowDown ArrowLeft ArrowRight Escape"
                    onClick={(e) => {
                      if (isFocusPointMode) {
                        handleFocusPointClick(e);
                      }
                    }}
                    onKeyDown={handleFocusPointKeyDown}
                    className="aspect-[4/3] group rounded-lg overflow-hidden relative border border-gray-200 outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
                  >
                    <p className="group-hover:opacity-100 opacity-0 transition-opacity absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-sm text-center font-medium bg-black/50 text-white px-2 py-1 rounded">
                      {t("clickToChangeFocus")}
                    </p>
                    {previewImages && (
                      <img
                        ref={imageRef}
                        onClick={() => {
                          setIsFocusPointMode(true);
                        }}
                        src={previewImages}
                        style={{
                          ...imageCropDomStyle(
                            objectFit,
                            focusPoint,
                            cropScale
                          ),
                        }}
                        alt={t("previewAlt")}
                        className="w-full h-full "
                      />
                    )}
                    {isFocusPointMode && (
                      <div className="absolute inset-0 bg-black/20 flex items-center justify-center">
                        <div className="text-white text-center p-2 bg-black/50 rounded">
                          <p className="text-sm font-medium pointer-events-none">
                            {t("focusPointInstruction")}
                          </p>
                        </div>

                        <div
                          className="absolute w-8 h-8 border-2 border-white rounded-full transform -translate-x-1/2 -translate-y-1/2 pointer-events-none"
                          style={{
                            left: `${focusPoint.x}%`,
                            top: `${focusPoint.y}%`,
                            boxShadow: "0 0 0 2px rgba(0,0,0,0.5)",
                          }}
                        >
                          <div className="absolute inset-0 flex items-center justify-center">
                            <div className="w-2 h-2 bg-white rounded-full"></div>
                          </div>
                          <div className="absolute w-16 h-0.5 bg-white/70 left-1/2 -translate-x-1/2"></div>
                          <div className="absolute w-0.5 h-16 bg-white/70 top-1/2 -translate-y-1/2"></div>
                        </div>
                      </div>
                    )}
                  </div>
                  {/* Edit Image  */}
                  {/* Object Fit */}
                  {
                    <div>
                      <h3 className="text-sm font-medium mb-2">{t("fitMode")}</h3>
                      <div className="flex gap-4">
                        <Button
                          variant="outline"
                          aria-pressed={objectFit === "cover"}
                          className={cn(
                            objectFit === "cover" &&
                              "bg-blue-50 border-blue-500"
                          )}
                          onClick={() => handleFitChange("cover")}
                        >
                          {t("fit.cover")}
                        </Button>
                        <Button
                          variant="outline"
                          aria-pressed={objectFit === "contain"}
                          className={cn(
                            objectFit === "contain" &&
                              "bg-blue-50 border-blue-500"
                          )}
                          onClick={() => handleFitChange("contain")}
                        >
                          {t("fit.contain")}
                        </Button>
                        <Button
                          variant="outline"
                          aria-pressed={objectFit === "fill"}
                          className={cn(
                            objectFit === "fill" && "bg-blue-50 border-blue-500"
                          )}
                          onClick={() => handleFitChange("fill")}
                        >
                          {t("fit.fill")}
                        </Button>
                      </div>
                    </div>
                  }
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-medium">{t("cropScale")}</h3>
                      <span className="text-xs tabular-nums text-gray-500">
                        {cropScale.toFixed(1)}×
                      </span>
                    </div>
                    <input
                      aria-label={t("cropScale")}
                      className="w-full accent-blue-600"
                      max={6}
                      min={0.1}
                      onChange={(event) =>
                        handleCropScaleChange(Number(event.target.value))
                      }
                      step={0.1}
                      type="range"
                      value={cropScale}
                    />
                    <div className="flex items-center justify-between">
                      <p className="text-xs text-gray-500">
                        {t("cropScaleDescription")}
                      </p>
                      <Button
                        size="sm"
                        type="button"
                        variant="outline"
                        onClick={() => handleCropScaleChange(1)}
                      >
                        {t("reset")}
                      </Button>
                    </div>
                  </div>
                  {isFocusPointMode && (
                    <Button type="button" size="sm" onClick={toggleFocusPointMode}>
                      {t("done")}
                    </Button>
                  )}
                  {/* Focus Point */}
                  {}
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
