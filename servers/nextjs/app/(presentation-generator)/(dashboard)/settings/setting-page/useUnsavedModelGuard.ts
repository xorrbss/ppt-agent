import { useEffect } from "react";
import { notify } from "@/components/ui/sonner";
import { LLMConfig } from "@/types/llm_config";

/**
 * Keeps the user on the settings page while the selected provider still has no
 * model chosen. Extracted verbatim from SettingPage.
 */
export const useUnsavedModelGuard = (llmConfig: LLMConfig, pathname: string) => {
  useEffect(() => {

    if (
      (llmConfig.LLM === "codex" && !llmConfig.CODEX_MODEL) ||
      (llmConfig.LLM === "openai" && !llmConfig.OPENAI_MODEL) ||
      (llmConfig.LLM === "google" && !llmConfig.GOOGLE_MODEL) ||
      (llmConfig.LLM === "vertex" && !llmConfig.VERTEX_MODEL) ||
      (llmConfig.LLM === "azure" && !llmConfig.AZURE_OPENAI_MODEL) ||
      (llmConfig.LLM === "bedrock" && !llmConfig.BEDROCK_MODEL) ||
      (llmConfig.LLM === "openrouter" && !llmConfig.OPENROUTER_MODEL) ||
      (llmConfig.LLM === "fireworks" && !llmConfig.FIREWORKS_MODEL) ||
      (llmConfig.LLM === "together" && !llmConfig.TOGETHER_MODEL) ||
      (llmConfig.LLM === "cerebras" && !llmConfig.CEREBRAS_MODEL) ||
      (llmConfig.LLM === "litellm" && !llmConfig.LITELLM_MODEL) ||
      (llmConfig.LLM === "lmstudio" && !llmConfig.LMSTUDIO_MODEL) ||
      (llmConfig.LLM === "anthropic" && !llmConfig.ANTHROPIC_MODEL) ||
      (llmConfig.LLM === "ollama" && !llmConfig.OLLAMA_MODEL) ||
      (llmConfig.LLM === "custom" && !llmConfig.CUSTOM_MODEL)
    ) {
      notify.error("설정을 저장할 수 없습니다", "선택한 제공자의 모델을 선택해 주세요");

      const currentUrl = window.location.href;

      const handleBeforeUnload = (e: BeforeUnloadEvent) => {
        console.log("beforeunload");
        e.preventDefault();
        e.returnValue = "";
      };

      const handleClick = (e: MouseEvent) => {


        const target = e.target as HTMLElement | null;
        const link = target?.closest("a");

        if (!link) return;

        const href = link.getAttribute("href");
        const targetAttr = link.getAttribute("target");

        if (
          href &&
          href !== "#" &&
          !href.startsWith("javascript:") &&
          targetAttr !== "_blank"
        ) {

          // notify.error("설정을 저장할 수 없습니다", "선택한 제공자에 사용할 모델을 선택해 주세요");
          e.preventDefault();
          window.history.pushState(null, "", pathname);
        }
      };

      const handlePopState = () => {
        console.log("popstate");
        window.history.pushState(null, "", pathname);
      };

      window.addEventListener("beforeunload", handleBeforeUnload);
      window.addEventListener("popstate", handlePopState);
      document.addEventListener("click", handleClick, true);

      // keep current page in history
      window.history.pushState(null, "", currentUrl);

      return () => {
        window.removeEventListener("beforeunload", handleBeforeUnload);
        window.removeEventListener("popstate", handlePopState);
        document.removeEventListener("click", handleClick, true);
      };
    }

  }, [llmConfig, pathname]);
};
