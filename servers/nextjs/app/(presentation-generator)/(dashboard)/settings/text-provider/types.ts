import { LLMConfig } from "@/types/llm_config";

export interface OpenAIConfigProps {
  onInputChange: (value: string | boolean, field: string) => void;
  llmConfig: LLMConfig;
}

export interface ModelOption {
  value: string;
  label: string;
  size?: string;
}
