/**
 * Upload entry experience: a centered creation composer followed by a
 * container-responsive template catalog. Generation behavior remains owned by
 * useUploadPageState; this component only reorganizes the interaction surface.
 */

"use client";

import React, { useCallback } from "react";
import { ArrowUp, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { useDispatch, useSelector } from "react-redux";

import Wrapper from "@/components/Wrapper";
import { Button } from "@/components/ui/button";
import { OverlayLoader } from "@/components/ui/overlay-loader";
import { cn } from "@/lib/utils";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import type { RootState } from "@/store/store";

import { AUTHORED_TEMPLATE_ID } from "../../outline/components/TemplateSelection";
import { ConfigurationSelects } from "./ConfigurationSelects";
import CurrentConfig from "./CurrentConfig";
import { PromptInput } from "./PromptInput";
import SupportingDoc from "./SupportingDoc";
import TemplateCatalog from "./TemplateCatalog";
import { abortDocumentProcessing } from "./upload-page/abortDocumentProcessing";
import { useSupportingFiles } from "./upload-page/useSupportingFiles";
import { useUploadPageState } from "./upload-page/useUploadPageState";

const UploadPage = () => {
  const t = useTranslations("upload");
  const dispatch = useDispatch();
  const selectedTemplateId = useSelector(
    (state: RootState) => state.pptGenUpload.selectedTemplate
  );
  const selectedThemeId = useSelector(
    (state: RootState) => state.pptGenUpload.selectedTheme
  );
  const authoredStyleId = useSelector(
    (state: RootState) => state.pptGenUpload.authoredStyle
  );
  const isAuthoredMode = selectedTemplateId === AUTHORED_TEMPLATE_ID;

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

  const selectStructured = useCallback(
    (selectionId: string) =>
      dispatch(setPptGenUploadState({ selectedTemplate: selectionId })),
    [dispatch]
  );
  const selectAuthored = useCallback(
    (styleId: string) =>
      dispatch(
        setPptGenUploadState({
          selectedTemplate: AUTHORED_TEMPLATE_ID,
          authoredStyle: styleId,
        })
      ),
    [dispatch]
  );

  return (
    <Wrapper className="max-w-none pb-20">
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
            ? () => abortDocumentProcessing(decomposeAbortControllerRef.current)
            : undefined
        }
      />

      <main className="px-4 sm:px-6 lg:px-8">
        <section
          className="flex flex-col items-center pt-10 md:pt-16"
          aria-labelledby="upload-hero-title"
        >
          <div className="w-full max-w-[760px] text-center">
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-[#E7E8EB] bg-[#FAFAFB] px-3 py-1.5 text-xs font-semibold text-[#5E626A]">
              <Sparkles
                className="h-3.5 w-3.5 text-[#6B5CE7]"
                aria-hidden="true"
              />
              {t("eyebrow")}
            </div>
            <h1
              id="upload-hero-title"
              className="font-syne text-[28px] font-bold leading-tight tracking-[-0.035em] text-[#242629] md:text-[36px] md:leading-[1.3]"
            >
              {t("heroTitle")}
            </h1>
            <p className="mx-auto mt-2 max-w-xl font-syne text-sm leading-6 text-[#737780] md:text-[15px]">
              {t("heroDescription")}
            </p>
          </div>

          <div
            className="mt-6 flex max-w-full flex-wrap items-center justify-center gap-2"
            role="group"
            aria-label={t("modeLabel")}
          >
            <div className="flex rounded-xl border border-[#E6E7EA] bg-white p-1 shadow-sm">
              <button
                type="button"
                aria-pressed={!isAuthoredMode}
                onClick={() => {
                  if (isAuthoredMode) {
                    dispatch(setPptGenUploadState({ selectedTemplate: null }));
                  }
                }}
                className={cn(
                  "min-h-9 rounded-lg px-4 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5]/30",
                  !isAuthoredMode
                    ? "bg-[#F1F2F4] text-[#242629]"
                    : "text-[#6E727A] hover:bg-[#F7F7F8]"
                )}
                data-testid="structured-mode-button"
              >
                {t("templateMode")}
              </button>
              <button
                type="button"
                aria-pressed={isAuthoredMode}
                onClick={() => selectAuthored(authoredStyleId)}
                className={cn(
                  "min-h-9 rounded-lg px-4 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5]/30",
                  isAuthoredMode
                    ? "bg-[#F1F2F4] text-[#242629]"
                    : "text-[#6E727A] hover:bg-[#F7F7F8]"
                )}
                data-testid="authored-mode-button"
              >
                {t("authoredMode")}
              </button>
            </div>
          </div>

          <div
            className={cn(
              "mt-4 rounded-2xl border bg-white p-4 transition-colors md:p-5",
              isDragOver
                ? "border-[#5141E5] ring-2 ring-[#5141E5]/20"
                : "border-[#E4E6E9]"
            )}
            style={{
              inlineSize: "min(760px, 100%)",
              minBlockSize: 114,
              boxShadow: "0 6px 30px rgb(0 0 0 / 8%)",
            }}
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

            <div className="mt-2">
              <SupportingDoc
                files={[...files]}
                onFilesChange={setFiles}
                compact
              />
            </div>

            <div className="mt-4 flex flex-col gap-3 border-t border-[#F0F1F3] pt-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
                <ConfigurationSelects
                  config={config}
                  onConfigChange={handleConfigChange}
                />
                <div className="min-w-0 max-w-full overflow-hidden">
                  <CurrentConfig />
                </div>
              </div>
              <Button
                type="button"
                onClick={handleGeneratePresentation}
                disabled={loadingState.isLoading}
                data-testid="generate-presentation-button"
                className="h-11 shrink-0 rounded-full bg-[#242629] px-5 font-syne text-sm font-semibold text-white shadow-[0_6px_16px_rgba(36,38,41,0.18)] transition hover:bg-[#111214] disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span>
                  {loadingState.isLoading ? t("generating") : t("generate")}
                </span>
                <ArrowUp className="ml-2 h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
          </div>
        </section>

        <TemplateCatalog
          selectedTemplateId={selectedTemplateId}
          selectedThemeId={selectedThemeId}
          authoredStyleId={authoredStyleId}
          onSelectStructured={selectStructured}
          onSelectAuthored={selectAuthored}
          onSelectTheme={(themeId) =>
            dispatch(setPptGenUploadState({ selectedTheme: themeId }))
          }
        />
      </main>
    </Wrapper>
  );
};

export default UploadPage;
