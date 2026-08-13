import http from "http";

export const RUNTIME_SETTINGS_REFRESH_PATH =
  "/_presenton/runtime-settings/refresh";
const RUNTIME_SETTINGS_REFRESH_TOKEN_HEADER =
  "x-presenton-runtime-settings-token";
// A newly atomically replaced credential file may require Windows ACL
// verification (whoami/icacls/fallback) before FastAPI can safely read it.
const RUNTIME_SETTINGS_REFRESH_TIMEOUT_MS = 60000;

export function requestRuntimeSettingsRefresh(
  fastApiPort: number,
  token: string,
  userConfig?: UserConfig,
): Promise<void> {
  if (!Number.isInteger(fastApiPort) || fastApiPort < 1 || fastApiPort > 65535) {
    return Promise.reject(new Error("Invalid backend runtime settings refresh port"));
  }
  if (!token) {
    return Promise.reject(new Error("Backend runtime settings refresh is unavailable"));
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) {
        return;
      }
      settled = true;
      callback();
    };
    const fail = (message: string) => finish(() => reject(new Error(message)));

    const body = userConfig === undefined ? "" : JSON.stringify(userConfig);
    const request = http.request(
      {
        hostname: "127.0.0.1",
        port: fastApiPort,
        path: RUNTIME_SETTINGS_REFRESH_PATH,
        method: "POST",
        headers: {
          [RUNTIME_SETTINGS_REFRESH_TOKEN_HEADER]: token,
          "content-type": "application/json",
          "content-length": Buffer.byteLength(body),
        },
      },
      (response) => {
        response.resume();
        response.once("error", () => {
          fail("Backend runtime settings refresh response failed");
        });
        response.once("end", () => {
          if (response.statusCode === 204) {
            finish(resolve);
            return;
          }
          fail(
            `Backend runtime settings refresh failed with status ${response.statusCode ?? "unknown"}`,
          );
        });
      },
    );

    request.once("error", () => {
      fail("Backend runtime settings refresh request failed");
    });
    request.setTimeout(RUNTIME_SETTINGS_REFRESH_TIMEOUT_MS, () => {
      request.destroy();
      fail("Backend runtime settings refresh request timed out");
    });
    request.end(body);
  });
}
