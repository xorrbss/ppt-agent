"use client";
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
    <div className="h-screen font-syne flex flex-col overflow-hidden relative">
      <main className="w-full mx-auto gap-6   overflow-hidden flex ">
        <SettingSideBar
          mode={mode}
          setMode={setMode}
          selectedProvider={selectedProvider}
          setSelectedProvider={setSelectedProvider}
        />
        <div className="w-full">
          <div className="sticky top-0 right-0 z-50 py-[28px]   backdrop-blur mb-4 ">
            <div className="flex  gap-3 items-center ">
              <h3 className=" text-[28px] tracking-[-0.84px] font-unbounded font-normal text-black flex items-center gap-2">
                설정
              </h3>
              <p className="text-[10px] px-2.5 py-0.5 rounded-[50px] text-[#7A5AF8] border border-[#EDEEEF]  font-medium ">
                {textSummary} · {imageSummary}
              </p>
            </div>
          </div>

          {mode === 'nanobanana' && <div className=" w-full bg-[#F9F8F8] p-7 rounded-[20px]">
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
