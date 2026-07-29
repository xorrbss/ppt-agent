import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import { resolveAppDataDirectory } from "./app-data-directory.ts";

test("explicit app-data directory has highest priority", () => {
  assert.equal(
    resolveAppDataDirectory({
      APP_DATA_DIRECTORY: " ./runtime-data ",
      USER_CONFIG_PATH: "./ignored/userConfig.json",
    }),
    path.resolve("./runtime-data")
  );
});

test("native development derives app-data from the user config path", () => {
  assert.equal(
    resolveAppDataDirectory({
      USER_CONFIG_PATH: " ./app_data/userConfig.json ",
    }),
    path.resolve("./app_data")
  );
});

test("missing app-data configuration fails before spawning the exporter", () => {
  assert.throws(() => resolveAppDataDirectory({}), /APP_DATA_DIRECTORY/);
});
