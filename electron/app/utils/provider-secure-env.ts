import path from "path";

export type ProviderSecureEnv = {
  PROVIDER_SECURE_MODE: "true";
  PROVIDER_SECURE_CREDENTIALS_REQUIRED: "true";
  PROVIDER_CREDENTIAL_BACKEND: "desktop_vault";
  PROVIDER_DESKTOP_VAULT_DIRECTORY: string;
  PROVIDER_CREDENTIAL_SCOPE_USER_ID: string;
};

export function buildProviderSecureEnv(appDataDirectory: string): ProviderSecureEnv {
  if (!path.isAbsolute(appDataDirectory)) {
    throw new Error("Provider secure storage requires an absolute app-data path");
  }
  return {
    PROVIDER_SECURE_MODE: "true",
    PROVIDER_SECURE_CREDENTIALS_REQUIRED: "true",
    PROVIDER_CREDENTIAL_BACKEND: "desktop_vault",
    PROVIDER_CREDENTIAL_SCOPE_USER_ID: "00000000-0000-4000-8000-000000000001",
    PROVIDER_DESKTOP_VAULT_DIRECTORY: path.join(
      appDataDirectory,
      "provider-credential-vault",
    ),
  };
}
