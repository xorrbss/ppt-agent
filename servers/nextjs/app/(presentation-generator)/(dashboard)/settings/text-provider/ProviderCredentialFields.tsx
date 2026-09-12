import CodexConfig from "@/components/codex-config";
import { LLMConfig } from "@/types/llm_config";
import { LLM_PROVIDER_OPTIONS, type PublicCredentialDescriptor } from "@/utils/provider-registry.generated";
import { Eye, EyeOff } from "lucide-react";
import { useTranslations } from "next-intl";
import React from "react";

interface ProviderCredentialFieldsProps {
  selectedProvider: keyof typeof LLM_PROVIDER_OPTIONS;
  llmConfig: LLMConfig;
  onInputChange: (value: string | boolean, field: string) => void;
  credentialFields: readonly PublicCredentialDescriptor[];
  showApiKey: boolean;
  setShowApiKey: React.Dispatch<React.SetStateAction<boolean>>;
}

const ProviderCredentialFields = ({ selectedProvider, llmConfig, onInputChange, credentialFields, showApiKey, setShowApiKey }: ProviderCredentialFieldsProps) => {
  const t = useTranslations("providerSettings");
  return <div className="flex w-full flex-col gap-3">
    {credentialFields.map((credential) => {
      const value = ((llmConfig as Record<string, unknown>)[credential.settingKey] as string) || "";
      if (credential.widget === "oauth-status") {
        // ChatGPT is the one OAuth provider whose sign-in runs from this app, so
        // it renders its own flow; other OAuth providers only get the notice.
        if (selectedProvider !== "codex") {
          return <p key={credential.settingKey} className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">{t("serverManagedSignIn")}</p>;
        }
        return <CodexConfig
          key={credential.settingKey}
          variant="settings"
          codexModel={llmConfig.CODEX_MODEL || ""}
          codexReasoningEffort={llmConfig.CODEX_REASONING_EFFORT || ""}
          onInputChange={(nextValue, field) => onInputChange(nextValue, field === "codex_model" ? "CODEX_MODEL" : field === "codex_reasoning_effort" ? "CODEX_REASONING_EFFORT" : field)}
        />;
      }
      const sensitive = credential.inputType === "password";
      return <label key={credential.settingKey} className="block text-sm font-medium text-gray-700">
        {credential.label}{credential.required ? " *" : ""}
        <div className="relative mt-2">
          <input
            type={sensitive && !showApiKey ? "password" : credential.inputType}
            value={value}
            required={credential.required}
            onChange={(event) => onInputChange(event.target.value, credential.settingKey)}
            className="w-full rounded-xl border border-zinc-200 bg-white px-3 py-3 pr-12 text-sm outline-none transition-colors focus:border-zinc-400 focus:ring-2 focus:ring-zinc-900/10"
          />
          {sensitive ? <button type="button" aria-label={t("toggleCredentialVisibility")} onClick={() => setShowApiKey((current) => !current)} className="absolute right-1 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-lg bg-white text-zinc-500 transition-colors hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400">{showApiKey ? <Eye className="h-4 w-4 text-gray-500" /> : <EyeOff className="h-4 w-4 text-gray-500" />}</button> : null}
        </div>
      </label>;
    })}
  </div>;
};

export default ProviderCredentialFields;
