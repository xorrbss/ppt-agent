import {
  LLM_PROVIDER_OPTIONS,
  PROVIDER_MODEL_BINDINGS,
  PUBLIC_PROVIDER_CREDENTIAL_DESCRIPTORS,
} from "@/utils/provider-registry.generated";
import { useMemo } from "react";

export const useProviderFields = (
  selectedProvider: keyof typeof LLM_PROVIDER_OPTIONS | null
) =>
  useMemo(() => {
    if (selectedProvider === null) {
      return {
        credentialFields: [],
        currentModelField: "",
        currentApiKeyField: "",
        isManualModelProvider: true,
      } as const;
    }
    const credentials = PUBLIC_PROVIDER_CREDENTIAL_DESCRIPTORS[selectedProvider] || [];
    const apiKey = credentials.find(
      (credential) => credential.kind === "secret" || credential.kind === "oauth"
    );
    const modelBinding = PROVIDER_MODEL_BINDINGS[selectedProvider];
    return {
      credentialFields: credentials,
      currentModelField: modelBinding?.settingKey || "",
      currentApiKeyField: apiKey?.settingKey || "",
      isManualModelProvider: modelBinding?.manual ?? true,
    };
  }, [selectedProvider]);
