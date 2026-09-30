export interface CodexModel {
  id: string;
  name: string;
}

export type CodexReasoningEffort = "low" | "medium" | "high";
export type CodexGenerationPresetId =
  | "luna-medium"
  | "luna-high"
  | "sol-low"
  | "sol-medium"
  | "sol-high"
  | "sol6-low"
  | "sol6-medium"
  | "sol6-high"
  | "astra-low"
  | "astra-medium";

export interface CodexGenerationPreset {
  id: CodexGenerationPresetId;
  modelId: string;
  effort: CodexReasoningEffort;
  name: string;
}

export const CHATGPT_MODELS: CodexModel[] = [
  { id: "gpt-5.6-sol", name: "GPT-5.6 Sol" },
  { id: "gpt-6.1-sol", name: "GPT-6.1 Sol" },
  { id: "gpt-5.6-luna", name: "GPT-5.6 Luna" },
  { id: "gpt-5.5", name: "GPT-5.5" },
  { id: "gpt-5.4", name: "GPT-5.4" },
  { id: "gpt-5.4-mini", name: "GPT-5.4 mini" },
  { id: "gpt-5.3-codex", name: "GPT-5.3 Codex" },
  { id: "gpt-5.3-codex-spark", name: "GPT-5.3 Codex Spark" },
  { id: "gpt-5.2", name: "GPT-5.2" },
];

/** PPT generation presets exposed by the ChatGPT-backed provider. */
export const CODEX_GENERATION_PRESETS: CodexGenerationPreset[] = [
  { id: "luna-medium", modelId: "gpt-6.1-sol", effort: "medium", name: "GPT-6.1 · Medium" },
  { id: "luna-high", modelId: "gpt-6.1-sol", effort: "high", name: "GPT-6.1 · High" },
  { id: "sol-low", modelId: "gpt-6.1-sol", effort: "low", name: "GPT-6.1 · Low" },
  { id: "sol-medium", modelId: "gpt-6.1-sol", effort: "medium", name: "GPT-6.1 · Medium" },
  { id: "sol-high", modelId: "gpt-6.1-sol", effort: "high", name: "GPT-6.1 · High" },
  // sol6-* are a legacy alias of sol-* (same gpt-6.1-sol target): kept so stored
  // configs/requests carrying the old sol6-* id still resolve identically.
  { id: "sol6-low", modelId: "gpt-6.1-sol", effort: "low", name: "GPT-6.1 · Low" },
  { id: "sol6-medium", modelId: "gpt-6.1-sol", effort: "medium", name: "GPT-6.1 · Medium" },
  { id: "sol6-high", modelId: "gpt-6.1-sol", effort: "high", name: "GPT-6.1 · High" },
  { id: "astra-low", modelId: "gpt-6.1-sol", effort: "low", name: "GPT-6.1 · Low" },
  { id: "astra-medium", modelId: "gpt-6.1-sol", effort: "medium", name: "GPT-6.1 · Medium" },
];

export const DEFAULT_CODEX_MODEL = "gpt-6.1-sol";
