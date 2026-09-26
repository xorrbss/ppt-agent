"use client";
import { useTranslations as useA2bTranslations } from "next-intl";
import React from "react";
import SettingSideBar from "./SettingSideBar";
import TextProvider from "./TextProvider";
import ImageProvider from "./ImageProvider";
import PrivacySettings from "./PrivacySettings";
import AdminPanel from "../admin/AdminPanel";
import DownloadProgressModal from "./setting-page/DownloadProgressModal";
import SaveConfigButton from "./setting-page/SaveConfigButton";
import SessionSection from "./setting-page/SessionSection";
import { getImageSummary, getTextSummary } from "./setting-page/providerSummaries";
import { useSettingsPageState } from "./setting-page/useSettingsPageState";
import { useUnsavedModelGuard } from "./setting-page/useUnsavedModelGuard";

const SettingsPage = () => {
  const tA2b = useA2bTranslations("dashboardExtras");
  const {
    pathname,
    mode,
    setMode,
    selectedProvider,
    setSelectedProvider,
    llmConfig,
    setLlmConfig,
    handleLlmInputChange,
    canChangeKeys,
    buttonState,
    downloadingModel,
    showDownloadModal,
    downloadAbortRef,
    downloadProgress,
    handleSaveConfig,
  } = useSettingsPageState();

  useUnsavedModelGuard(llmConfig, pathname, canChangeKeys);

  if (!canChangeKeys) {
    return null;
  }

  const textSummary = getTextSummary(llmConfig);

  const imageSummary = getImageSummary(llmConfig);


  return (
    <div className="relative flex min-h-screen flex-col bg-[#FAFAFA] font-syne">
      <main className="mx-auto flex w-full min-w-0 flex-col gap-6 p-4 sm:p-6 lg:flex-row lg:gap-8 lg:p-8">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 lg:hidden">설정</h1>
        <SettingSideBar
          mode={mode}
          setMode={setMode}
          selectedProvider={selectedProvider}
          setSelectedProvider={setSelectedProvider}
        />
        <div className="min-w-0 flex-1">
          <div className="mb-6 hidden min-h-12 items-center py-2 lg:flex">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-zinc-900 sm:text-3xl">
                {tA2b("settings")}
              </h3>
              <p className="rounded-full border border-violet-100 bg-violet-50 px-3 py-1 text-xs font-medium text-violet-700">
                {textSummary} · {imageSummary}
              </p>
            </div>
          </div>

          {mode === 'nanobanana' && <div className="w-full rounded-2xl border border-zinc-200/80 bg-white p-5 sm:p-7">
            <h4>Nano Banana</h4>
          </div>}
          {mode === 'presenton' && selectedProvider === 'text-provider' && <TextProvider
            onInputChange={handleLlmInputChange}
            llmConfig={llmConfig}
          />}
          {mode === 'presenton' && selectedProvider === 'image-provider' && <ImageProvider llmConfig={llmConfig} setLlmConfig={setLlmConfig} />}
          {selectedProvider === 'privacy' && <PrivacySettings />}
          {selectedProvider === "admin" && <AdminPanel embedded />}
          {selectedProvider === "session" && <SessionSection />}

        </div>
      </main>

      {/* Fixed Bottom Button — hidden on Sign out; nothing to save there */}
      {!["session", "admin"].includes(selectedProvider) ? (
        <SaveConfigButton buttonState={buttonState} onSave={handleSaveConfig} />
      ) : null}

      {/* Download Progress Modal */}
      {showDownloadModal && downloadingModel && (
        <DownloadProgressModal
          downloadingModel={downloadingModel}
          downloadProgress={downloadProgress}
          modelName={llmConfig.OLLAMA_MODEL}
          onCancel={() => downloadAbortRef.current?.abort()}
        />
      )}
    </div>
  );
};

export default SettingsPage;
