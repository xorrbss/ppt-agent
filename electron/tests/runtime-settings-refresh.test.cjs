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
