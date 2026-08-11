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

const ProviderCredentialFields = ({ llmConfig, onInputChange, credentialFields, showApiKey, setShowApiKey }: ProviderCredentialFieldsProps) => {
  const t = useTranslations("providerSettings");
  return <div className="flex w-full flex-col gap-3">
    {credentialFields.map((credential) => {
      const value = ((llmConfig as Record<string, unknown>)[credential.settingKey] as string) || "";
      if (credential.widget === "oauth-status") {
        return <p key={credential.settingKey} className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">{t("serverManagedSignIn")}</p>;
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
            className="w-full rounded-lg border border-gray-300 px-2 py-3 pr-10 outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
          />
          {sensitive ? <button type="button" aria-label={t("toggleCredentialVisibility")} onClick={() => setShowApiKey((current) => !current)} className="absolute right-2 top-1/2 -translate-y-1/2 bg-white px-2 py-1">{showApiKey ? <Eye className="h-4 w-4 text-gray-500" /> : <EyeOff className="h-4 w-4 text-gray-500" />}</button> : null}
        </div>
      </label>;
    })}
  </div>;
};

export default ProviderCredentialFields;
