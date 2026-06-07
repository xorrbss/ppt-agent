/**
 * UploadPage Component
 * 
 * This component handles the presentation generation upload process, allowing users to:
 * - Configure presentation settings (slides, language)
 * - Input prompts
 * - Upload supporting documents
 * 
 * @component
 */

"use client";
import React, { useState, useMemo } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useDispatch, useSelector } from "react-redux";
import { clearOutlines, setPresentationId } from "@/store/slices/presentationGeneration";
import { PromptInput } from "./PromptInput";
import { LanguageType, PresentationConfig, ToneType, VerbosityType } from "../type";
import SupportingDoc from "./SupportingDoc";
import { Button } from "@/components/ui/button";
import { ChevronRight } from "lucide-react";
import { notify } from "@/components/ui/sonner";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { OverlayLoader } from "@/components/ui/overlay-loader";
import Wrapper from "@/components/Wrapper";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { ConfigurationSelects } from "./ConfigurationSelects";
import { RootState } from "@/store/store";
import { ImagesApi } from "../../services/api/images";
import CurrentConfig from "./CurrentConfig";
import { LLMConfig } from "@/types/llm_config";
import TemplateSelection from "../../outline/components/TemplateSelection";
import { resolveTemplateSelection, templateSelectionToId } from "@/app/presentation-templates/select";
import ThemeGallery from "./ThemeGallery";
import { DEFAULT_THEMES } from "@/app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/constants";

const STOCK_IMAGE_PROVIDERS = new Set(["pexels", "pixabay"]);
const FILE_TYPE_WORD = new Set([".doc", ".docx", ".docm", ".odt", ".rtf"]);
const FILE_TYPE_PRESENTATION = new Set([".ppt", ".pptx", ".pptm", ".odp"]);
const FILE_TYPE_SPREADSHEET = new Set([".xls", ".xlsx", ".xlsm", ".ods", ".csv", ".tsv"]);
const FILE_TYPE_IMAGE = new Set([".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg"]);
const FILE_TYPE_PDF = new Set([".pdf"]);
const FILE_TYPE_TEXT = new Set([".txt"]);

// Types for loading state
interface LoadingState {
  isLoading: boolean;
  message: string;
  duration?: number;
  showProgress?: boolean;
  extra_info?: string;
}

const getFileExtension = (fileName: string): string => {
  const index = fileName.lastIndexOf(".");
  if (index < 0) return "";
  return fileName.slice(index).toLowerCase();
};

const getFileCategory = (file: File): string => {
  const extension = getFileExtension(file.name || "");
  if (FILE_TYPE_WORD.has(extension)) return "word";
  if (FILE_TYPE_PRESENTATION.has(extension)) return "presentation";
  if (FILE_TYPE_SPREADSHEET.has(extension)) return "spreadsheet";
  if (FILE_TYPE_IMAGE.has(extension) || (file.type || "").startsWith("image/")) return "image";
  if (FILE_TYPE_PDF.has(extension) || file.type === "application/pdf") return "pdf";
  if (FILE_TYPE_TEXT.has(extension) || file.type === "text/plain") return "text";
  return "other";
};

const getSelectedTextModel = (config?: LLMConfig): string => {
  if (!config) return "";
  switch (config.LLM) {
    case "openai":
      return config.OPENAI_MODEL || "";
    case "google":
      return config.GOOGLE_MODEL || "";
    case "vertex":
      return config.VERTEX_MODEL || "";
    case "azure":
      return config.AZURE_OPENAI_MODEL || "";
    case "bedrock":
      return config.BEDROCK_MODEL || "";
    case "openrouter":
      return config.OPENROUTER_MODEL || "";
    case "fireworks":
      return config.FIREWORKS_MODEL || "";
    case "together":
      return config.TOGETHER_MODEL || "";
    case "cerebras":
      return config.CEREBRAS_MODEL || "";
    case "litellm":
      return config.LITELLM_MODEL || "";
    case "lmstudio":
      return config.LMSTUDIO_MODEL || "";
    case "anthropic":
      return config.ANTHROPIC_MODEL || "";
    case "ollama":
      return config.OLLAMA_MODEL || "";
    case "custom":
      return config.CUSTOM_MODEL || "";
    case "codex":
      return config.CODEX_MODEL || "";
    default:
      return "";
  }
};

const getSelectedImageQuality = (config?: LLMConfig): string => {
  if (!config) return "";
  if (config.IMAGE_PROVIDER === "dall-e-3") return config.DALL_E_3_QUALITY || "";
  if (config.IMAGE_PROVIDER === "gpt-image-1.5") return config.GPT_IMAGE_1_5_QUALITY || "";
  return "";
};

const UploadPage = () => {
  const router = useRouter();
  const pathname = usePathname();
  const dispatch = useDispatch();
  const llmConfig = useSelector((state: RootState) => state.userConfig.llm_config);
  const selectedTemplateId = useSelector((state: RootState) => state.pptGenUpload.selectedTemplate);
  const selectedThemeId = useSelector((state: RootState) => state.pptGenUpload.selectedTheme);
  const selectedTemplate = useMemo(
    () => resolveTemplateSelection(selectedTemplateId),
    [selectedTemplateId]
  );

  const [files, setFiles] = useState<File[]>([]);
  const [config, setConfig] = useState<PresentationConfig>({
    slides: null,
    language: LanguageType.Korean,
    prompt: "",
    tone: ToneType.Default,
    verbosity: VerbosityType.Standard,
    instructions: "",
    includeTableOfContents: false,
    includeTitleSlide: false,
    webSearch: false,
  });

  const [loadingState, setLoadingState] = useState<LoadingState>({
    isLoading: false,
    message: "",
    duration: 4,
    showProgress: false,
    extra_info: "",
  });

  const getUploadSnapshotProps = () => {
    const trimmedPrompt = config.prompt.trim();
    const trimmedInstructions = (config.instructions || "").trim();
    const attachmentCategories = Array.from(new Set(files.map(getFileCategory))).sort();
    const imageGenerationEnabled = !llmConfig?.DISABLE_IMAGE_GENERATION;
    const parsedSlides =
      config.slides && /^\d+$/.test(config.slides) ? Number(config.slides) : null;

    return {
      pathname,
      generation_path: files.length > 0 ? "documents" : "prompt_only",
      slides_selected: parsedSlides,
      slides_mode: config.slides ? "selected" : "auto",
      language: config.language || "",
      tone: config.tone,
      verbosity: config.verbosity,
      include_table_of_contents: !!config.includeTableOfContents,
      include_title_slide: !!config.includeTitleSlide,
      web_search: !!config.webSearch,
      has_prompt: Boolean(trimmedPrompt),
      prompt_char_count: trimmedPrompt.length,
      prompt_word_count: trimmedPrompt ? trimmedPrompt.split(/\s+/).filter(Boolean).length : 0,
      has_instructions: Boolean(trimmedInstructions),
      instructions_char_count: trimmedInstructions.length,
      has_attachments: files.length > 0,
      attachments_count: files.length,
      attachment_categories: attachmentCategories.join(","),
      text_provider: llmConfig?.LLM || "",
      text_model: getSelectedTextModel(llmConfig),
      image_generation_enabled: imageGenerationEnabled,
      image_provider: imageGenerationEnabled ? (llmConfig?.IMAGE_PROVIDER || "") : "disabled",
      image_quality: imageGenerationEnabled ? getSelectedImageQuality(llmConfig) : "",
    };
  };

  const trackUploadValidationFailure = (reason: string) => {
    trackEvent(MixpanelEvent.Upload_Configuration_Invalid, {
      ...getUploadSnapshotProps(),
      reason,
    });
  };

  const handleConfigChange = (key: keyof PresentationConfig, value: unknown) => {
    setConfig((prev) => ({ ...prev, [key]: value } as PresentationConfig));
  };

  // Best-effort: theme is presentation-level (applied at render), so a PATCH after
  // create is enough for the editor/export to render with it. No preset → no-op.
  const applySelectedTheme = async (presentationId: string) => {
    if (!selectedThemeId) return;
    const preset = DEFAULT_THEMES.find((t: any) => t.id === selectedThemeId);
    if (!preset) return;
    try {
      await PresentationGenerationApi.updatePresentationContent({ id: presentationId, theme: preset });
    } catch (error) {
      console.error("Failed to apply selected theme preset", error);
    }
  };

  const ensureStockImageProviderReady = async (): Promise<boolean> => {
    if (llmConfig?.DISABLE_IMAGE_GENERATION) {
      return true;
    }

    const selectedProvider = (llmConfig?.IMAGE_PROVIDER || "").toLowerCase();
    if (!STOCK_IMAGE_PROVIDERS.has(selectedProvider)) {
      return true;
    }

    try {
      const providerApiKey =
        selectedProvider === "pexels"
          ? llmConfig?.PEXELS_API_KEY
          : llmConfig?.PIXABAY_API_KEY;
      await ImagesApi.searchStockImages("business", 1, {
        provider: selectedProvider,
        apiKey: providerApiKey,
        strictApiKey: true,
      });
      return true;
    } catch (error: any) {
      notify.error(
        "이미지 제공자에 연결할 수 없습니다",
        error?.message ||
        `현재 ${selectedProvider}에 연결할 수 없습니다. API 키와 설정을 확인한 후 다시 시도해 주세요.`
      );
      return false;
    }
  };

  /**
   * Validates the current configuration and files
   * @returns boolean indicating if the configuration is valid
   */
  const validateConfiguration = (): boolean => {
    if (!config.language) {
      trackUploadValidationFailure("language_missing");
      notify.warning("언어를 선택해 주세요", "언어를 선택해 주세요.");
      return false;
    }

    if (files.length > 0 && config.language === LanguageType.Auto) {
      trackUploadValidationFailure("language_auto_with_documents");
      notify.warning("언어를 선택해 주세요", "업로드한 문서를 처리하기 전에 언어를 선택해 주세요.");
      return false;
    }

    if (!config.prompt.trim() && files.length === 0) {
      trackUploadValidationFailure("prompt_or_document_missing");
      notify.warning("입력이 필요합니다", "프롬프트를 입력하거나 문서를 하나 이상 업로드해 주세요.");
      return false;
    }
    return true;
  };

  /**
   * Handles the presentation generation process
   */
  const handleGeneratePresentation = async () => {
    if (!validateConfiguration()) return;
    trackEvent(MixpanelEvent.Upload_Generation_Started, getUploadSnapshotProps());


    const isStockProviderReady = await ensureStockImageProviderReady();
    if (!isStockProviderReady) {
      trackUploadValidationFailure("stock_image_provider_unreachable");
      return;
    }

    try {
      const hasUploadedAssets = files.length > 0;

      if (hasUploadedAssets) {
        await handleDocumentProcessing();
      } else {
        await handleDirectPresentationGeneration();
      }
    } catch (error) {
      handleGenerationError(error);
    }
  };

  /**
   * Handles document processing
   */
  const handleDocumentProcessing = async () => {
    setLoadingState({
      isLoading: true,
      message: "문서를 처리하는 중…",
      showProgress: true,
      duration: 90,
      extra_info: files.length > 0 ? "용량이 큰 문서는 몇 분 정도 걸릴 수 있습니다." : "",
    });

    let documents = [];

    if (files.length > 0) {
      const uploadResponse = await PresentationGenerationApi.uploadDoc(files);
      documents = uploadResponse;
    }

    const selectedLanguage = config?.language ?? "";

    const promises: Promise<any>[] = [];

    if (documents.length > 0) {
      promises.push(
        PresentationGenerationApi.decomposeDocuments(
          documents,
          selectedLanguage
        )
      );
    }
    const responses = await Promise.all(promises);
    dispatch(setPptGenUploadState({
      config,
      files: responses,
    }));
    dispatch(clearOutlines())

    // Silently create the presentation from the decomposed documents and go
    // straight to the auto outline bridge (skip the /documents-preview step).
    const documentPaths = responses
      .flat()
      .filter((item: any) => item && item.name && item.file_path)
      .map((item: any) => item.file_path);

    const createResponse = await PresentationGenerationApi.createPresentation({
      content: config?.prompt ?? "",
      n_slides: config?.slides ? parseInt(config.slides, 10) : null,
      file_paths: documentPaths,
      language: selectedLanguage,
      tone: config?.tone,
      verbosity: config?.verbosity,
      instructions: config?.instructions || null,
      include_table_of_contents: !!config?.includeTableOfContents,
      include_title_slide: !!config?.includeTitleSlide,
      web_search: !!config?.webSearch,
    });

    await applySelectedTheme(createResponse.id);
    dispatch(setPresentationId(createResponse.id));
    trackEvent(MixpanelEvent.Upload_Documents_Processed, {
      ...getUploadSnapshotProps(),
      uploaded_documents_count: documents.length,
      decompose_job_count: responses.length,
      presentation_id: createResponse.id,
      destination: "/outline?auto=1",
    });
    trackEvent(MixpanelEvent.Navigation, { from: pathname, to: "/outline?auto=1" });
    router.push("/outline?auto=1");
  };

  /**
   * Handles direct presentation generation without documents
   */
  const handleDirectPresentationGeneration = async () => {
    setLoadingState({
      isLoading: true,
      message: "개요를 생성하는 중…",
      showProgress: true,
      duration: 30,
    });

    const selectedLanguage = config?.language ?? "";

    // Use the first available layout group for direct generation
    const createResponse = await PresentationGenerationApi.createPresentation({
      content: config?.prompt ?? "",
      n_slides: config?.slides ? parseInt(config.slides, 10) : null,
      file_paths: [],
      language: selectedLanguage,
      tone: config?.tone,
      verbosity: config?.verbosity,
      instructions: config?.instructions || null,
      include_table_of_contents: !!config?.includeTableOfContents,
      include_title_slide: !!config?.includeTitleSlide,
      web_search: !!config?.webSearch,
    });


    await applySelectedTheme(createResponse.id);
    dispatch(setPresentationId(createResponse.id));
    dispatch(clearOutlines())
    trackEvent(MixpanelEvent.Upload_Outline_Generation_Requested, {
      ...getUploadSnapshotProps(),
      presentation_id: createResponse.id,
      destination: "/outline?auto=1",
    });
    trackEvent(MixpanelEvent.Navigation, { from: pathname, to: "/outline?auto=1" });
    router.push("/outline?auto=1");
  };

  /**
   * Handles errors during presentation generation
   */
  const handleGenerationError = (error: any) => {
    console.error("Error in upload page", error);
    setLoadingState({
      isLoading: false,
      message: "",
      duration: 0,
      showProgress: false,
    });
    notify.error(
      "생성에 실패했습니다",
      error.message || "발표자료를 시작하는 중 문제가 발생했습니다."
    );
  };

  return (
    <Wrapper className="pb-28 lg:max-w-[60%] xl:max-w-[54%]">
      <OverlayLoader
        show={loadingState.isLoading}
        text={loadingState.message}
        showProgress={loadingState.showProgress}
        duration={loadingState.duration}
        extra_info={loadingState.extra_info}
      />
      <div className="flex flex-col items-center pt-6 md:pt-10">
        <h1 className="text-center text-2xl md:text-[32px] font-semibold tracking-[-0.02em] text-[#101828] font-syne">
          무엇을 만들어 드릴까요?
        </h1>
        <p className="mt-2 text-center text-sm text-[#667085] font-syne">
          아이디어를 입력하고 필요하면 파일을 첨부하세요. 발표자료를 만들어 드립니다.
        </p>

        <div className="mt-7 w-full rounded-2xl border border-[#EAECF0] bg-white p-4 shadow-[0_4px_24px_rgba(16,24,40,0.06)]">
          <PromptInput
            value={config.prompt}
            onChange={(value) => handleConfigChange("prompt", value)}
            onSubmit={handleGeneratePresentation}
          />

          <div className="mt-3">
            <SupportingDoc
              files={[...files]}
              onFilesChange={setFiles}
              compact
            />
          </div>

          <div className="mt-4 flex flex-col gap-3 border-t border-[#F2F4F7] pt-3 sm:flex-row sm:items-center sm:justify-between">
            <ConfigurationSelects
              config={config}
              onConfigChange={handleConfigChange}
            />
            <CurrentConfig />
          </div>
        </div>

        <div className="mt-8 w-full">
          <h2 className="mb-3 text-base font-semibold text-[#101828] font-syne">템플릿 선택</h2>
          <TemplateSelection
            selectedTemplate={selectedTemplate}
            onSelectTemplate={(t) =>
              dispatch(setPptGenUploadState({ selectedTemplate: templateSelectionToId(t) }))
            }
          />
        </div>

        <div className="mt-8 w-full">
          <h2 className="mb-3 text-base font-semibold text-[#101828] font-syne">
            테마 프리셋 <span className="text-sm font-normal text-[#667085]">(선택)</span>
          </h2>
          <ThemeGallery
            selectedTheme={selectedThemeId}
            onSelectTheme={(id) => dispatch(setPptGenUploadState({ selectedTheme: id }))}
          />
        </div>

        <div className="mt-6 flex w-full justify-center">
          <Button
            onClick={handleGeneratePresentation}
            style={{
              background: "linear-gradient(270deg, #D5CAFC 2.4%, #E3D2EB 27.88%, #F4DCD3 69.23%, #FDE4C2 100%)"
            }}
            className="rounded-[28px] flex items-center justify-center py-5 px-8 text-[#101323] font-syne font-semibold text-sm"
          >
            <span>생성하기</span>
            <ChevronRight className="!w-5 !h-5 " />
          </Button>
        </div>
      </div>
    </Wrapper>
  );
};

export default UploadPage;
