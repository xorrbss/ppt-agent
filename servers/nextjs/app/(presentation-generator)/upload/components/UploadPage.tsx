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
import React, { useMemo } from "react";
import { useDispatch, useSelector } from "react-redux";
import { PromptInput } from "./PromptInput";
import SupportingDoc from "./SupportingDoc";
import { Button } from "@/components/ui/button";
import { ChevronDown, ChevronRight } from "lucide-react";
import { OverlayLoader } from "@/components/ui/overlay-loader";
import Wrapper from "@/components/Wrapper";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { ConfigurationSelects } from "./ConfigurationSelects";
import { RootState } from "@/store/store";
import CurrentConfig from "./CurrentConfig";
import TemplateSelection from "../../outline/components/TemplateSelection";
import { resolveTemplateSelection, templateSelectionToId } from "@/app/presentation-templates/select";
import ThemeGallery from "./ThemeGallery";
import { abortDocumentProcessing } from "./upload-page/abortDocumentProcessing";
import { useSupportingFiles } from "./upload-page/useSupportingFiles";
import { useUploadPageState } from "./upload-page/useUploadPageState";
import { useTranslations } from "next-intl";

const UploadPage = () => {
  const t = useTranslations("upload");
  const dispatch = useDispatch();
  const selectedTemplateId = useSelector((state: RootState) => state.pptGenUpload.selectedTemplate);
  const selectedThemeId = useSelector((state: RootState) => state.pptGenUpload.selectedTheme);
  const selectedTemplate = useMemo(
    () => resolveTemplateSelection(selectedTemplateId),
    [selectedTemplateId]
  );

  const {
    files,
    setFiles,
    isDragOver,
    handleCardDragOver,
    handleCardDragLeave,
    handleCardDrop,
  } = useSupportingFiles();

  const {
    config,
    handleConfigChange,
    loadingState,
    canCancelDocumentProcessing,
    decomposeAbortControllerRef,
    handleGeneratePresentation,
  } = useUploadPageState({ files, selectedThemeId });

  return (
    <Wrapper className="pb-28 lg:max-w-[60%] xl:max-w-[54%]">
      <OverlayLoader
        show={loadingState.isLoading}
        text={loadingState.message}
        showProgress={loadingState.showProgress}
        duration={loadingState.duration}
        progress={loadingState.progress}
        progressLabel={loadingState.progressLabel}
        extra_info={loadingState.extra_info}
        onCancel={
          canCancelDocumentProcessing
            ? () =>
                abortDocumentProcessing(decomposeAbortControllerRef.current)
            : undefined
        }
      />
      <div className="flex flex-col items-center pt-6 md:pt-10">
        <h1 className="text-center text-2xl md:text-[32px] font-semibold tracking-[-0.02em] text-[#101828] font-syne">
          무엇을 만들어 드릴까요?
        </h1>
        <p className="mt-2 text-center text-sm text-[#667085] font-syne">
          아이디어를 입력하고 필요하면 파일을 첨부하세요. 발표자료를 만들어 드립니다.
        </p>

        <div
          className={`mt-7 w-full rounded-2xl border bg-white p-4 shadow-[0_4px_24px_rgba(16,24,40,0.06)] transition-colors ${
            isDragOver
              ? "border-[#5146E5] ring-2 ring-[#5146E5]/20"
              : "border-[#EAECF0]"
          }`}
          onDragOver={handleCardDragOver}
          onDragLeave={handleCardDragLeave}
          onDrop={handleCardDrop}
          data-testid="compose-card"
        >
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

        <details
          className="group mt-6 w-full rounded-2xl border border-[#EAECF0] bg-white shadow-[0_4px_24px_rgba(16,24,40,0.04)]"
          data-testid="appearance-settings"
        >
          <summary
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                const details = event.currentTarget.parentElement;
                if (details instanceof HTMLDetailsElement) {
                  details.open = !details.open;
                }
              }
            }}
            className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-2xl px-4 py-4 font-syne focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5146E5]/30 [&::-webkit-details-marker]:hidden"
          >
            <span>
              <span className="block text-sm font-semibold text-[#101828]">{t("designSettings.title")}</span>
              <span className="mt-0.5 block text-xs font-normal text-[#667085]">
                {t("designSettings.description")}
              </span>
            </span>
            <ChevronDown className="h-5 w-5 shrink-0 text-[#667085] transition-transform group-open:rotate-180" aria-hidden="true" />
          </summary>

          <div className="border-t border-[#F2F4F7] px-4 pb-5">
            <div className="mt-5 w-full">
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
          </div>
        </details>

        <div
          className="fixed bottom-4 left-1/2 z-40 flex w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 items-center justify-between gap-3 rounded-2xl border border-[#E4E7EC] bg-white/95 p-3 shadow-[0_12px_36px_rgba(16,24,40,0.18)] backdrop-blur sm:w-[calc(100%-3rem)]"
          data-testid="generate-action-bar"
        >
          <p className="min-w-0 text-xs text-[#667085] font-syne sm:text-sm">
            {t("generateSummary")}
          </p>
          <Button
            type="button"
            onClick={handleGeneratePresentation}
            disabled={loadingState.isLoading}
            data-testid="generate-presentation-button"
            style={{
              background: "linear-gradient(270deg, #D5CAFC 2.4%, #E3D2EB 27.88%, #F4DCD3 69.23%, #FDE4C2 100%)"
            }}
            className="flex shrink-0 items-center justify-center rounded-[28px] px-5 py-5 text-sm font-semibold text-[#101323] font-syne disabled:cursor-not-allowed disabled:opacity-60 sm:px-8"
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
