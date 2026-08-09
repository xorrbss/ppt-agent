import React, { useCallback, useState, useEffect } from "react";
import { notify } from "@/components/ui/sonner";
import { RootState } from "@/store/store";
import { useSelector } from "react-redux";
import {
  getLLMConfigValidationError,
  handleSaveLLMConfig,
} from "@/utils/storeHelpers";
import {
  checkIfSelectedOllamaModelIsPulled,
  pullOllamaModel,
} from "@/utils/providerUtils";
import { useRouter, usePathname } from "next/navigation";
import { LLMConfig } from "@/types/llm_config";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { ImagesApi } from "@/app/(presentation-generator)/services/api/images";
import { getApiUrl } from "@/utils/api";
import type {
  ButtonState,
  DownloadingModel,
  SettingsMode,
  SettingsSection,
} from "./types";

const STOCK_IMAGE_PROVIDERS = new Set(["pexels", "pixabay"]);

export const useSettingsPageState = () => {
  const router = useRouter();
  const pathname = usePathname();
  const [mode, setMode] = useState<SettingsMode>('presenton')
  const [selectedProvider, setSelectedProvider] =
    useState<SettingsSection>("text-provider");
  const userConfigState = useSelector((state: RootState) => state.userConfig);
  const [llmConfig, setLlmConfig] = useState<LLMConfig>(
    userConfigState.llm_config
  );
  const handleLlmInputChange = useCallback(
    (value: string | boolean, field: string) => {
      setLlmConfig((prev) => {
        if (prev[field as keyof LLMConfig] === value) {
          return prev;
        }

        return {
          ...prev,
          [field]: value,
        };
      });
    },
    []
  );
  const canChangeKeys = userConfigState.can_change_keys;
  const [buttonState, setButtonState] = useState<ButtonState>({
    isLoading: false,
    isDisabled: false,
    text: "설정 저장",
    showProgress: false,
  });

  const [downloadingModel, setDownloadingModel] =
    useState<DownloadingModel | null>(null);
  const [showDownloadModal, setShowDownloadModal] = useState<boolean>(false);
  const downloadAbortRef = React.useRef<AbortController | null>(null);

  const downloadProgress = React.useMemo(() => {
    if (
      downloadingModel &&
      downloadingModel.downloaded !== null &&
      downloadingModel.size !== null
    ) {
      return Math.round(
        (downloadingModel.downloaded / downloadingModel.size) * 100
      );
    }
    return 0;
  }, [downloadingModel]);

  const ensureSelectedStockProviderReady = async (): Promise<boolean> => {
    if (llmConfig.DISABLE_IMAGE_GENERATION) {
      return true;
    }

    const provider = (llmConfig.IMAGE_PROVIDER || "").toLowerCase();
    if (!STOCK_IMAGE_PROVIDERS.has(provider)) {
      return true;
    }

    const providerApiKey =
      provider === "pexels" ? llmConfig.PEXELS_API_KEY : llmConfig.PIXABAY_API_KEY;

    try {
      await ImagesApi.searchStockImages("business", 1, {
        provider,
        apiKey: providerApiKey,
        strictApiKey: true,
      });
      return true;
    } catch (error: any) {
      notify.error(
        "설정을 저장할 수 없습니다",
        error?.message ||
        `제공된 API 키로 ${provider}에 연결할 수 없습니다. 설정을 확인하고 다시 시도해 주세요.`
      );
      return false;
    }
  };


  const checkCurrentAuthStatus = async () => {
    try {
      const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/status"));
      if (!res.ok) {
        return false;
      }
      const data = await res.json();
      if (data.status === "authenticated") {
        return true;
      } else {
        return false;
      }
    } catch {
      return false;
    }
  };
  const handleSaveConfig = async () => {

    if (llmConfig.LLM === 'codex') {
      const isAuthenticated = await checkCurrentAuthStatus();
      if (!isAuthenticated) {
        notify.error("로그인이 필요합니다", "계속하려면 ChatGPT에 로그인해 주세요.");
        return;
      }
    }
    trackEvent(MixpanelEvent.Settings_SaveConfiguration_Button_Clicked, {
      pathname,
    });
    const validationError = getLLMConfigValidationError(llmConfig);
    if (validationError) {
      notify.warning("설정을 저장할 수 없습니다", validationError);
      if (
        selectedProvider === "image-provider" &&
        llmConfig.LLM === "openai" &&
        !String(llmConfig.OPENAI_MODEL || "").trim()
      ) {
        setSelectedProvider("text-provider");
      }
      return;
    }

    const providerReady = await ensureSelectedStockProviderReady();
    if (!providerReady) {
      return;
    }

    try {
      setButtonState((prev) => ({
        ...prev,
        isLoading: true,
        isDisabled: true,
        text: "설정 저장 중...",
      }));
      trackEvent(MixpanelEvent.Settings_SaveConfiguration_API_Call);
      await handleSaveLLMConfig(llmConfig);
      let ollamaModelDownloaded = false;
      if (llmConfig.LLM === "ollama" && llmConfig.OLLAMA_MODEL) {
        trackEvent(MixpanelEvent.Settings_CheckOllamaModelPulled_API_Call);
        const isPulled = await checkIfSelectedOllamaModelIsPulled(
          llmConfig.OLLAMA_MODEL
        );
        if (!isPulled) {
          setShowDownloadModal(true);
          setDownloadingModel({
            name: llmConfig.OLLAMA_MODEL || "",
            size: null,
            downloaded: null,
            status: "pulling",
            done: false,
          });
          trackEvent(MixpanelEvent.Settings_DownloadOllamaModel_API_Call);
          const downloadOutcome = await handleModelDownload();
          if (downloadOutcome === "cancelled") {
            return;
          }
          ollamaModelDownloaded = downloadOutcome === "completed";
        }
      }
      notify.success(
        ollamaModelDownloaded ? "설정 저장 및 모델 준비 완료" : "설정 저장됨",
        ollamaModelDownloaded
          ? "설정이 저장되었고 Ollama 모델 다운로드가 완료되었습니다."
          : "설정이 성공적으로 저장되었습니다."
      );
      setButtonState((prev) => ({
        ...prev,
        isLoading: false,
        isDisabled: false,
        text: "설정 저장",
      }));
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "저장하는 중 문제가 발생했습니다.";
      notify.error("설정을 저장하지 못했습니다", message);
      setButtonState((prev) => ({
        ...prev,
        isLoading: false,
        isDisabled: false,
        text: "설정 저장",
      }));
    }
  };

  const handleModelDownload = async (): Promise<"completed" | "cancelled"> => {
    const ac = new AbortController();
    downloadAbortRef.current = ac;
    try {
      await pullOllamaModel(
        llmConfig.OLLAMA_MODEL!,
        setDownloadingModel,
        ac.signal
      );
      return "completed";
    } catch (e) {
      const aborted = e instanceof Error && e.name === "AbortError";
      if (aborted) {
        setDownloadingModel(null);
        setShowDownloadModal(false);
        setButtonState({
          isLoading: false,
          isDisabled: false,
          text: "설정 저장",
          showProgress: false,
        });
        notify.info(
          "다운로드 취소됨",
          "Ollama 모델 다운로드가 중지되었습니다. 설정은 이미 저장되었으며, 다시 저장하면 다운로드를 재시도할 수 있습니다."
        );
        return "cancelled";
      }
      setDownloadingModel(null);
      setShowDownloadModal(false);
      throw e;
    } finally {
      downloadAbortRef.current = null;
    }
  };

  useEffect(() => {
    if (
      downloadingModel &&
      downloadingModel.downloaded !== null &&
      downloadingModel.size !== null
    ) {
      const percentage = Math.round(
        (downloadingModel.downloaded / downloadingModel.size) * 100
      );
      setButtonState({
        isLoading: true,
        isDisabled: true,
        text: `모델 다운로드 중 (${percentage}%)`,
        showProgress: true,
        progressPercentage: percentage,
        status: downloadingModel.status,
      });
    }

    if (downloadingModel && downloadingModel.done) {
      setTimeout(() => {
        setShowDownloadModal(false);
        setDownloadingModel(null);
      }, 2000);
    }
  }, [downloadingModel]);

  useEffect(() => {
    if (!canChangeKeys) {
      router.push("/dashboard");
    }
  }, [canChangeKeys, router]);

  return {
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
  };
};
