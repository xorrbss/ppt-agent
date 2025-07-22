"use client";
import React, { useState, useEffect } from "react";
import Header from "../dashboard/components/Header";
import Wrapper from "@/components/Wrapper";
import { Settings, Key, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { RootState } from "@/store/store";
import { useSelector } from "react-redux";
import { handleSaveLLMConfig } from "@/utils/storeHelpers";
import {
  checkIfSelectedOllamaModelIsPulled,
  pullOllamaModel,
  LLMConfig
} from "@/utils/providerUtils";
import { useRouter } from "next/navigation";
import LLMProviderSelection from "@/components/LLMSelection";

// Button state interface
interface ButtonState {
  isLoading: boolean;
  isDisabled: boolean;
  text: string;
  showProgress: boolean;
  progressPercentage?: number;
  status?: string;
}

const SettingsPage = () => {
  const router = useRouter();
  const userConfigState = useSelector((state: RootState) => state.userConfig);
  const [llmConfig, setLlmConfig] = useState<LLMConfig>(userConfigState.llm_config);
  const canChangeKeys = userConfigState.can_change_keys;
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [buttonState, setButtonState] = useState<ButtonState>({
    isLoading: false,
    isDisabled: false,
    text: "Save Configuration",
    showProgress: false
  });

  const [downloadingModel, setDownloadingModel] = useState<{
    name: string;
    size: number | null;
    downloaded: number | null;
    status: string;
    done: boolean;
  } | null>(null);

  const handleSaveConfig = async () => {
    try {
      setIsLoading(true);
      setButtonState(prev => ({
        ...prev,
        isLoading: true,
        isDisabled: true,
        text: "Saving Configuration..."
      }));

      await handleSaveLLMConfig(llmConfig);

      if (llmConfig.LLM === "ollama" && llmConfig.OLLAMA_MODEL) {
        const isPulled = await checkIfSelectedOllamaModelIsPulled(llmConfig.OLLAMA_MODEL);
        if (!isPulled) {
          await handleModelDownload();
        }
      }

      toast.success("Configuration saved successfully");
      setIsLoading(false);
      setButtonState(prev => ({
        ...prev,
        isLoading: false,
        isDisabled: false,
        text: "Save Configuration"
      }));
      router.back();
    } catch (error) {
      console.error("Error:", error);
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to save configuration"
      );
      setIsLoading(false);
      setButtonState(prev => ({
        ...prev,
        isLoading: false,
        isDisabled: false,
        text: "Save Configuration"
      }));
    }
  };

  const handleModelDownload = async () => {
    try {
      await pullOllamaModel(llmConfig.OLLAMA_MODEL!, setDownloadingModel);
    } catch (error) {
      console.error("Error downloading model:", error);
      setDownloadingModel(null);
    }
  };

  useEffect(() => {
    if (downloadingModel && downloadingModel.downloaded !== null && downloadingModel.size !== null) {
      const percentage = Math.round(((downloadingModel.downloaded / downloadingModel.size) * 100));
      setButtonState({
        isLoading: true,
        isDisabled: true,
        text: `Downloading Model (${percentage}%)`,
        showProgress: true,
        progressPercentage: percentage,
        status: downloadingModel.status
      });
    }

    if (downloadingModel && downloadingModel.done) {
      setTimeout(() => {
        setDownloadingModel(null);
        toast.success("Model downloaded successfully!");
      }, 2000);
    }
  }, [downloadingModel]);

  useEffect(() => {
    if (!canChangeKeys) {
      router.push("/dashboard");
    }
  }, [canChangeKeys, router]);

  if (!canChangeKeys) {
    return null;
  }

  return (
    <div className="min-h-screen bg-[#E9E8F8] font-instrument_sans">
      <Header />
      <Wrapper className="lg:w-[80%]">
        <div className="py-8 space-y-6">
          {/* Settings Header */}
          <div className="flex items-center gap-3 mb-6">
            <Settings className="w-8 h-8 text-blue-600" />
            <h1 className="text-2xl font-semibold text-gray-900">Settings</h1>
          </div>

          {/* API Configuration Section */}
          <div className="bg-white rounded-xl shadow-sm p-6">
            <div className="flex items-center gap-3 mb-6">
              <Key className="w-5 h-5 text-blue-600" />
              <h2 className="text-lg font-medium text-gray-900">
                API Configuration
              </h2>
            </div>

            {/* LLM Selection Component */}
            <div className="h-[600px]">
              <LLMProviderSelection
                initialLLMConfig={llmConfig}
                onConfigChange={setLlmConfig}
                buttonState={buttonState}
                setButtonState={setButtonState}
              />
            </div>

            {/* Save Button */}
            <button
              onClick={handleSaveConfig}
              disabled={buttonState.isDisabled}
              className={`mt-8 w-full font-semibold py-3 px-4 rounded-lg transition-all duration-500 ${buttonState.isDisabled
                ? "bg-gray-400 cursor-not-allowed"
                : "bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 focus:ring-4 focus:ring-blue-200"
                } text-white`}
            >
              {buttonState.isLoading ? (
                <div className="flex items-center justify-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {buttonState.text}
                </div>
              ) : (
                buttonState.text
              )}
            </button>

            {llmConfig.LLM === "ollama" &&
              downloadingModel?.status &&
              downloadingModel.status !== "pulled" && (
                <div className="mt-3 text-sm bg-green-100 rounded-lg p-2 font-semibold capitalize text-center text-gray-600">
                  {downloadingModel.status}
                </div>
              )}
          </div>
        </div>
      </Wrapper>
    </div>
  );
};

export default SettingsPage;
