export interface CodexModel {
  id: string;
  name: string;
}

export const CHATGPT_MODELS: CodexModel[] = [
  { id: "gpt-5.6-sol", name: "GPT-5.6 Sol" },
  { id: "gpt-5.5", name: "GPT-5.5" },
  { id: "gpt-5.4", name: "GPT-5.4" },
  { id: "gpt-5.4-mini", name: "GPT-5.4 mini" },
  { id: "gpt-5.3-codex", name: "GPT-5.3 Codex" },
  { id: "gpt-5.3-codex-spark", name: "GPT-5.3 Codex Spark" },
  { id: "gpt-5.2", name: "GPT-5.2" },
];

export const DEFAULT_CODEX_MODEL = "gpt-5.6-sol";
