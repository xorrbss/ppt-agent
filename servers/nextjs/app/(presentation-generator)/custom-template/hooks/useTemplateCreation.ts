import { useState, useCallback } from "react";
import { notify } from "@/components/ui/sonner";
import { getHeader, getHeaderForFormData } from "@/app/(presentation-generator)/services/api/header";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";
import {
    TemplateCreationStep,
    TemplateCreationState,
    FontData,
    FontUploadPreviewResponse,
    SlideLayoutResponse,
    UploadedFont,
    ProcessedSlide,
} from "../types";
import { getApiUrl } from "@/utils/api";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";

/** Must match `VISION_LAYOUT_ERROR_MARKER` in FastAPI `utils/template_vision_errors.py`. */
const TEMPLATE_VISION_MODEL_MARKER = "TEMPLATE_VISION_MODEL_REQUIRED";

const initialState: TemplateCreationState = {
    step: 'file-upload',
    isLoading: false,
    error: null,
    fontsData: null,
    previewData: null,
    templateId: null,
    totalSlides: 0,
    slideLayouts: [],
    currentSlideIndex: 0,
};


export const useTemplateCreation = () => {
    const [state, setState] = useState<TemplateCreationState>(initialState);
    const [uploadedFonts, setUploadedFonts] = useState<UploadedFont[]>([]);
    const [slides, setSlides] = useState<ProcessedSlide[]>([]);

    // Helper to update state partially
    const updateState = useCallback((updates: Partial<TemplateCreationState>) => {
        setState(prev => ({ ...prev, ...updates }));
    }, []);

    // Reset to initial state
    const reset = useCallback(() => {
        setState(initialState);
        setUploadedFonts([]);
        setSlides([]);
    }, []);

    // Step 1: Check fonts in PPTX file
    const checkFonts = useCallback(async (pptxFile: File): Promise<FontData | null> => {
        updateState({ isLoading: true, error: null });

        try {
            const extensionIndex = pptxFile.name.lastIndexOf(".");
            const fileExtension = extensionIndex >= 0 ? pptxFile.name.slice(extensionIndex).toLowerCase() : "";
            trackEvent(MixpanelEvent.CustomTemplate_Creation_Started, {
                source: "pptx_upload",
                file_name: pptxFile.name,
                file_size_bytes: pptxFile.size,
                file_extension: fileExtension,
            });
            const formData = new FormData();
            formData.append("pptx_file", pptxFile);

            const response = await fetch(getApiUrl(`/api/v1/ppt/fonts/check`), {
                method: "POST",
                headers: getHeaderForFormData(),
                body: formData,
            });

            const data = await ApiResponseHandler.handleResponse(
                response,
                "Failed to check fonts in the presentation"
            );

            updateState({
                fontsData: data,
                step: 'font-check',
                isLoading: false
            });

            return data;
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : "폰트 확인 실패";
            updateState({ error: errorMessage, isLoading: false });
            notify.error("폰트 확인 실패", errorMessage);
            return null;
        }
    }, [updateState]);


    const uploadFont = useCallback((fontName: string, file: File): string | null => {
        // Check if font is already added
        const existingFont = uploadedFonts.find((f) => f.fontName === fontName);
        if (existingFont) {
            notify.warning("이미 추가된 폰트", `"${fontName}" 폰트가 이미 업로드 목록에 있습니다.`);
            return fontName;
        }

        // Validate file type
        const validExtensions = [".ttf", ".otf", ".woff", ".woff2", ".eot"];
        const fileExtension = file.name.toLowerCase().substring(file.name.lastIndexOf("."));

        if (!validExtensions.includes(fileExtension)) {
            notify.error("잘못된 폰트 파일", ".ttf, .otf, .woff, .woff2 또는 .eot 파일을 업로드해 주세요.");
            return null;
        }

        // Validate file size (10MB limit)
        const maxSize = 10 * 1024 * 1024;
        if (file.size > maxSize) {
            notify.error("파일이 너무 큽니다", "폰트 파일 크기는 10MB 미만이어야 합니다.");
            return null;
        }

        // Store font locally
        const newFont: UploadedFont = {
            fontName: fontName,
            fontUrl: '', // Will be set after upload
            fontPath: '',
            file: file,
        };

        setUploadedFonts(prev => [...prev, newFont]);
        notify.success("폰트가 추가되었습니다", `"${fontName}" 폰트가 성공적으로 추가되었습니다.`);
        return fontName;
    }, [uploadedFonts]);

    // Remove a font
    const removeFont = useCallback((fontName: string) => {
        setUploadedFonts(prev => prev.filter(font => font.fontName !== fontName));
        notify.info("폰트가 제거되었습니다", "업로드 목록에서 폰트가 제거되었습니다.");
    }, []);

    // Get all unsupported fonts that need upload
    const getUnsupportedFonts = useCallback((): string[] => {
        if (!state.fontsData?.unavailable_fonts) {
            return [];
        }
        return state.fontsData.unavailable_fonts
            .map((font) => font.name)
            .filter(
                (fontName) =>
                    !uploadedFonts.some(
                        (uploaded) =>
                            uploaded.fontName === fontName ||
                            uploaded.fontName ===
                                state.fontsData?.unavailable_fonts.find(
                                    (f) => f.name === fontName
                                )?.original_name
                    )
            );
    }, [state.fontsData, uploadedFonts]);

    // Check if all required fonts are uploaded
    const allFontsUploaded = useCallback((): boolean => {
        return getUnsupportedFonts().length === 0;
    }, [getUnsupportedFonts]);

    // Step 2: Upload fonts and get slide preview
    const fontUploadAndPreview = useCallback(async (
        pptxFile: File
    ): Promise<FontUploadPreviewResponse | null> => {
        updateState({ isLoading: true, error: null, step: 'font-upload' });

        try {
            const formData = new FormData();
            formData.append("pptx_file", pptxFile);

            // Add uploaded font files (actual File objects)
            uploadedFonts.forEach(font => {
                formData.append("font_files", font.file);
                formData.append("original_font_names", font.fontName);
            });

            const response = await fetch(
                getApiUrl(`/api/v1/ppt/template/fonts-upload-and-slides-preview`),
                {
                    method: "POST",
                    headers: getHeaderForFormData(),
                    body: formData,
                }
            );

            const data = await ApiResponseHandler.handleResponse(
                response,
                "Failed to upload fonts and preview slides"
            );

            updateState({
                previewData: data,
                step: 'slides-preview',
                isLoading: false
            });

            notify.success("미리보기가 생성되었습니다", "슬라이드 미리보기가 성공적으로 생성되었습니다.");
            return data;
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : "Preview generation failed";
            updateState({ error: errorMessage, isLoading: false });
            notify.error("미리보기 실패", errorMessage);
            return null;
        }
    }, [uploadedFonts, updateState]);

    // Step 3: Initialize template creation
    const initTemplateCreation = useCallback(async (): Promise<string | null> => {
        if (!state.previewData) {
            notify.error("미리보기 데이터 없음", "계속하기 전에 미리보기를 생성하세요.");
            return null;
        }

        updateState({ isLoading: true, error: null, step: 'template-creation' });

        try {
            const response = await fetch(getApiUrl(`/api/v1/ppt/template/create/init`), {
                method: "POST",
                headers: getHeader(),
                body: JSON.stringify({
                    pptx_url: state.previewData.modified_pptx_url,
                    slide_image_urls: state.previewData.slide_image_urls,
                    fonts: state.previewData.fonts,
                }),
            });

            const data = await ApiResponseHandler.handleResponse(
                response,
                "Failed to initialize template creation"
            );

            // Initialize slides array based on preview images
            const initialSlides: ProcessedSlide[] = state.previewData.slide_image_urls.map(
                (url, index) => ({
                    slide_number: index + 1,
                    screenshot_url: url,
                    processing: false,
                    processed: false,
                })
            );

            setSlides(initialSlides);
            updateState({
                templateId: data.id || data,
                totalSlides: state.previewData.slide_image_urls.length,
                isLoading: false
            });
            trackEvent(MixpanelEvent.CustomTemplate_Creation_Started, {
                source: "template_init",
                template_id: typeof data === "string" ? data : data.id,
                total_slides: state.previewData.slide_image_urls.length,
                uploaded_font_count: state.previewData.fonts?.length || 0,
            });

            notify.success("템플릿이 초기화되었습니다", "템플릿 생성이 성공적으로 초기화되었습니다.");

            // Automatically start processing the first slide
            if (typeof data === 'string') {
                createSlideLayout(data, 0);
            } else if (data.id) {
                createSlideLayout(data.id, 0);
            }

            return typeof data === 'string' ? data : data.id;
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : "초기화 실패";
            updateState({ error: errorMessage, isLoading: false });
            notify.error("초기화 실패", errorMessage);
            // reset the state
            reset();
            return null;
        }
    }, [state.previewData, updateState]);

    // Step 4: Create slide layout for a specific slide (with auto-advance for initial processing)
    const createSlideLayout = useCallback(async (
        templateId: string,
        slideIndex: number,
        autoAdvance: boolean = true,
        retry: boolean = false,
        _isAutoRetry: boolean = false
    ): Promise<SlideLayoutResponse | null> => {
        // Mark slide as processing
        setSlides(prev => prev.map((s, i) =>
            i === slideIndex ? { ...s, processing: true, error: undefined } : s
        ));

        updateState({ currentSlideIndex: slideIndex });

        try {
            const startResponse = await fetch(
                getApiUrl(`/api/v1/ppt/template/slide-layout/create/start`),
                {
                    method: "POST",
                    headers: getHeader(),
                    body: JSON.stringify({
                        id: templateId,
                        index: slideIndex,
                    }),
                }
            );

            const startData = await ApiResponseHandler.handleResponse(
                startResponse,
                `Failed to start layout job for slide ${slideIndex + 1}`
            );
            const jobId = startData.job_id as string;

            const pollMs = 2000;
            const maxWaitMs = 45 * 60 * 1000;
            const deadline = Date.now() + maxWaitMs;
            let data: { react_component: string } | undefined;

            while (Date.now() < deadline) {
                const statusResponse = await fetch(
                    getApiUrl(`/api/v1/ppt/template/slide-layout/create/job/${encodeURIComponent(jobId)}`),
                    { headers: getHeader() }
                );
                const statusData = await ApiResponseHandler.handleResponse(
                    statusResponse,
                    `Failed to check layout job for slide ${slideIndex + 1}`
                );
                if (statusData.status === "complete" && statusData.react_component) {
                    data = { react_component: statusData.react_component };
                    break;
                }
                if (statusData.status === "failed") {
                    throw new Error(
                        statusData.error ||
                            `Layout generation failed for slide ${slideIndex + 1}`
                    );
                }
                await new Promise((r) => setTimeout(r, pollMs));
            }

            if (!data) {
                throw new Error(
                    "Timed out waiting for slide layout generation (exceeded 45 minutes)"
                );
            }

            const layoutResult: SlideLayoutResponse = {
                slide_index: slideIndex,
                react_component: data.react_component,
                layout_id: "",
                layout_name: "",
            };

            // Update slide with the react component
            setSlides(prev => {
                const newSlides = prev.map((s, i) =>
                    i === slideIndex ? {
                        ...s,
                        processing: false,
                        processed: true,
                        react: layoutResult.react_component,
                        layout_id: layoutResult.layout_id || undefined,
                        layout_name: layoutResult.layout_name || undefined,
                    } : s
                );

                // Only auto-advance during initial processing
                if (autoAdvance) {
                    const nextIndex = slideIndex + 1;
                    if (nextIndex < newSlides.length && !newSlides[nextIndex].processed) {
                        setTimeout(() => {
                            createSlideLayout(templateId, nextIndex, true);
                        }, 500);
                    } else {
                        // Check if all slides are processed
                        const allProcessed = newSlides.every(s => s.processed || s.error);
                        if (allProcessed) {
                            updateState({ step: 'completed' });
                            trackEvent(MixpanelEvent.CustomTemplate_Creation_Completed, {
                                template_id: templateId,
                                total_slides: newSlides.length,
                                processed_slides: newSlides.filter(s => s.processed).length,
                                failed_slides: newSlides.filter(s => Boolean(s.error)).length,
                            });
                            const failedCount = newSlides.filter(s => Boolean(s.error)).length;
                            const processedCount = newSlides.filter(s => s.processed).length;
                            if (failedCount > 0) {
                                notify.warning(
                                    "일부 슬라이드를 처리하지 못했습니다",
                                    `${newSlides.length}개 중 ${processedCount}개 슬라이드가 재구성되었습니다. ${failedCount}개 슬라이드가 실패했습니다 — 확인 후 다시 시도해 주세요.`
                                );
                            } else {
                                notify.success(
                                    "모든 슬라이드 처리 완료",
                                    "모든 슬라이드가 성공적으로 재구성되었습니다."
                                );
                            }
                        }
                    }
                } else {
                    // Single slide reconstruction - just show success
                    notify.success("슬라이드 재구성됨", `${slideIndex + 1}번 슬라이드가 성공적으로 재구성되었습니다.`);
                }

                return newSlides;
            });

            return layoutResult;
        } catch (error) {
            const errorMessage =
                error instanceof Error ? error.message : "Layout creation failed";
            const isVisionModelError = errorMessage.includes(TEMPLATE_VISION_MODEL_MARKER);

            // Auto-retry once on transient failures; vision/model capability errors won't recover.
            if (!_isAutoRetry && !isVisionModelError) {
                console.log(`Auto-retrying slide ${slideIndex + 1} after API failure...`);
                return createSlideLayout(templateId, slideIndex, autoAdvance, true, true);
            }

            // Mark slide with error
            setSlides(prev => {
                const newSlides = prev.map((s, i) =>
                    i === slideIndex ? { ...s, processing: false, error: errorMessage } : s
                );

                // Only auto-advance during initial processing
                if (autoAdvance) {
                    const nextIndex = slideIndex + 1;
                    if (nextIndex < newSlides.length && !newSlides[nextIndex].processed) {
                        setTimeout(() => {
                            createSlideLayout(templateId, nextIndex, true);
                        }, 500);
                    } else {
                        const allProcessed = newSlides.every(s => s.processed || s.error);
                        if (allProcessed) {
                            updateState({ step: 'completed' });
                        }
                    }
                }

                return newSlides;
            });

            if (isVisionModelError) {
                const description = errorMessage
                    .replace(TEMPLATE_VISION_MODEL_MARKER, "")
                    .trim()
                    .replace(/^\n+/, "");
                notify.error(
                    "비전 지원 텍스트 모델이 필요합니다",
                    description ||
                        "설정에서 이미지를 지원하는 텍스트 모델을 선택하고 저장한 뒤 다시 시도해 주세요.",
                    { duration: 12_000 }
                );
            } else {
                notify.error(`${slideIndex + 1}번 슬라이드 실패`, errorMessage);
            }
            return null;
        }
    }, [updateState]);

    // Reconstruct a single slide (no auto-advance)
    const retrySlide = useCallback((slideIndex: number) => {
        if (state.templateId) {
            // Pass false for autoAdvance to only reconstruct this specific slide
            createSlideLayout(state.templateId, slideIndex, false, true);
        }
    }, [state.templateId, createSlideLayout]);

    // Move to font upload step (when font check is done)
    const proceedToFontUpload = useCallback(() => {
        updateState({ step: 'font-upload' });
    }, [updateState]);

    // Calculate progress
    const completedSlides = slides.filter(s => s.processed || s.error).length;
    const progressPercentage = state.totalSlides > 0
        ? Math.round((completedSlides / state.totalSlides) * 100)
        : 0;

    return {
        // State
        state,
        uploadedFonts,
        slides,
        setSlides,

        // Progress
        completedSlides,
        progressPercentage,

        // Font operations
        checkFonts,
        uploadFont,
        removeFont,
        getUnsupportedFonts,
        allFontsUploaded,

        // Template creation operations
        fontUploadAndPreview,
        initTemplateCreation,
        createSlideLayout,
        retrySlide,

        // Navigation
        proceedToFontUpload,
        reset,
        updateState,
    };
};
