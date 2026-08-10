import { randomUUID } from "crypto";

export const PROVIDER_SETTINGS_PENDING_REFRESH_KEY =
  "_PRESENTON_PROVIDER_SETTINGS_PENDING_REFRESH";

export type PersistedUserConfig = UserConfig & Record<string, unknown>;

const CODEX_TOKEN_KEYS = [
  "CODEX_ACCESS_TOKEN",
  "CODEX_REFRESH_TOKEN",
  "CODEX_TOKEN_EXPIRES",
  "CODEX_ACCOUNT_ID",
] as const;

export function createProviderSettingsPendingRefreshNonce(): string {
  return randomUUID();
}

export function mergeUserConfig(
  existingConfig: PersistedUserConfig,
  userConfig: UserConfig,
  pendingRefreshNonce?: string,
): PersistedUserConfig {
  const definedIncomingEntries = Object.entries(userConfig).filter(
    ([, value]) => value !== undefined,
  );
  const nextConfig: PersistedUserConfig = {
    ...existingConfig,
    ...Object.fromEntries(definedIncomingEntries),
  };

  for (const key of CODEX_TOKEN_KEYS) {
    if (key in existingConfig) {
      nextConfig[key] = existingConfig[key];
    } else {
      delete nextConfig[key];
    }
  }

  if (pendingRefreshNonce !== undefined) {
    if (!pendingRefreshNonce) {
      throw new Error("Provider settings pending refresh nonce must be nonempty");
    }
    nextConfig[PROVIDER_SETTINGS_PENDING_REFRESH_KEY] = pendingRefreshNonce;
  } else if (PROVIDER_SETTINGS_PENDING_REFRESH_KEY in existingConfig) {
    nextConfig[PROVIDER_SETTINGS_PENDING_REFRESH_KEY] =
      existingConfig[PROVIDER_SETTINGS_PENDING_REFRESH_KEY];
  } else {
    delete nextConfig[PROVIDER_SETTINGS_PENDING_REFRESH_KEY];
  }

  return nextConfig;
}
