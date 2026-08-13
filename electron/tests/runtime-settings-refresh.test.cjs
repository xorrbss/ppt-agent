const assert = require("node:assert/strict");
const http = require("node:http");
const test = require("node:test");

const {
  RUNTIME_SETTINGS_REFRESH_PATH,
  requestRuntimeSettingsRefresh,
} = require("../app_dist/utils/runtime-settings-refresh.js");
const {
  saveUserConfigAndRefresh,
} = require("../app_dist/ipc/user_config_runtime.js");
const {
  PROVIDER_SETTINGS_PENDING_REFRESH_KEY,
  createProviderSettingsPendingRefreshNonce,
  mergeUserConfig,
} = require("../app_dist/utils/user-config-merge.js");

async function startServer(handler) {
  const server = http.createServer(handler);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.equal(typeof address, "object");
  return {
    port: address.port,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

test("runtime refresh sends only an authenticated empty loopback POST", async (t) => {
  const token = "runtime-control-token";
  let observed;
  const server = await startServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      observed = {
        method: request.method,
        url: request.url,
        token: request.headers["x-presenton-runtime-settings-token"],
        body: Buffer.concat(chunks).toString("utf8"),
      };
      response.writeHead(204);
      response.end();
    });
  });
  t.after(server.close);

  await requestRuntimeSettingsRefresh(server.port, token);

  assert.deepEqual(observed, {
    method: "POST",
    url: RUNTIME_SETTINGS_REFRESH_PATH,
    token,
    body: "",
  });
});

test("secure runtime refresh sends settings directly without a plaintext file", async (t) => {
  const token = "runtime-control-token";
  const userConfig = {
    LLM: "openai",
    OPENAI_API_KEY: "provider-api-key",
  };
  let observed;
  const server = await startServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      observed = {
        token: request.headers["x-presenton-runtime-settings-token"],
        contentType: request.headers["content-type"],
        body: JSON.parse(Buffer.concat(chunks).toString("utf8")),
      };
      response.writeHead(204);
      response.end();
    });
  });
  t.after(server.close);

  await requestRuntimeSettingsRefresh(server.port, token, userConfig);

  assert.deepEqual(observed, {
    token,
    contentType: "application/json",
    body: userConfig,
  });
});

test("secure IPC save forwards in memory and performs no compatibility write", async () => {
  const userConfig = { LLM: "google", GOOGLE_API_KEY: "provider-api-key" };
  const calls = [];

  await saveUserConfigAndRefresh(userConfig, {
    async refreshRuntimeSettings(config) {
      calls.push(config);
    },
  });

  assert.deepEqual(calls, [userConfig]);
});

test("runtime refresh errors never reflect backend bodies or the control token", async (t) => {
  const token = "do-not-reflect-this-token";
  const server = await startServer((_request, response) => {
    response.writeHead(500, { "content-type": "text/plain" });
    response.end("do-not-reflect-provider-api-key");
  });
  t.after(server.close);

  await assert.rejects(
    requestRuntimeSettingsRefresh(server.port, token),
    (error) => {
      assert.match(error.message, /status 500/);
      assert.doesNotMatch(error.message, /do-not-reflect/);
      return true;
    },
  );
});

test("the IPC save contract persists before refresh without forwarding config values", async () => {
  const calls = [];
  const userConfig = {
    LLM: "openai",
    OPENAI_API_KEY: "provider-api-key",
    OPENAI_MODEL: "new-model",
  };

  await saveUserConfigAndRefresh(userConfig, {
    persistUserConfig(config) {
      calls.push(["persist", config]);
    },
    async refreshRuntimeSettings(...args) {
      calls.push(["refresh", args]);
    },
  });

  assert.deepEqual(calls, [
    ["persist", userConfig],
    ["refresh", []],
  ]);
});

test("the IPC save contract rejects when the persisted config cannot be activated", async () => {
  let persisted = false;
  const activationError = new Error("Backend runtime settings refresh request failed");

  await assert.rejects(
    saveUserConfigAndRefresh(
      { LLM: "openai", OPENAI_MODEL: "new-model" },
      {
        persistUserConfig() {
          persisted = true;
        },
        async refreshRuntimeSettings() {
          throw activationError;
        },
      },
    ),
    activationError,
  );

  assert.equal(persisted, true);
});

test("the IPC save contract never refreshes after a failed file write", async () => {
  const persistenceError = new Error("Unable to persist user config");
  let refreshCalls = 0;

  await assert.rejects(
    saveUserConfigAndRefresh(
      { LLM: "google", GOOGLE_MODEL: "new-model" },
      {
        persistUserConfig() {
          throw persistenceError;
        },
        async refreshRuntimeSettings() {
          refreshCalls += 1;
        },
      },
    ),
    persistenceError,
  );

  assert.equal(refreshCalls, 0);
});

test("only an IPC persistence merge records a fresh provider settings pending marker", () => {
  const existingConfig = {
    LLM: "openai",
    CODEX_ACCESS_TOKEN: "preserved-access-token",
    unrelated: "preserved",
  };
  const incomingConfig = {
    LLM: "google",
    GOOGLE_MODEL: "new-model",
    CODEX_ACCESS_TOKEN: "must-not-overwrite",
    [PROVIDER_SETTINGS_PENDING_REFRESH_KEY]: "must-not-control-marker",
  };
  const firstNonce = createProviderSettingsPendingRefreshNonce();
  const secondNonce = createProviderSettingsPendingRefreshNonce();

  const startupConfig = mergeUserConfig(existingConfig, incomingConfig);
  const firstIpcConfig = mergeUserConfig(existingConfig, incomingConfig, firstNonce);
  const secondIpcConfig = mergeUserConfig(firstIpcConfig, incomingConfig, secondNonce);

  assert.equal(startupConfig[PROVIDER_SETTINGS_PENDING_REFRESH_KEY], undefined);
  assert.equal(firstIpcConfig[PROVIDER_SETTINGS_PENDING_REFRESH_KEY], firstNonce);
  assert.equal(secondIpcConfig[PROVIDER_SETTINGS_PENDING_REFRESH_KEY], secondNonce);
  assert.notEqual(firstNonce, secondNonce);
  assert.match(firstNonce, /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.equal(firstIpcConfig.CODEX_ACCESS_TOKEN, "preserved-access-token");
  assert.equal(firstIpcConfig.LLM, "google");
  assert.equal(firstIpcConfig.GOOGLE_MODEL, "new-model");
  assert.equal(firstIpcConfig.unrelated, "preserved");
});
