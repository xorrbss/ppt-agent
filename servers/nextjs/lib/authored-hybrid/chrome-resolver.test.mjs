import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { resolveAuthoredHybridChromeExecutable } from "./chrome-runtime-discovery.ts";

const ENVIRONMENT_KEYS = [
  "AUTHORED_HYBRID_CHROME_PATH",
  "PUPPETEER_EXECUTABLE_PATH",
  "CHROME_PATH",
  "PLAYWRIGHT_BROWSERS_PATH",
];

test("resolver prefers the newest Playwright headless shell over desktop Chrome", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "authored-chrome-resolver-"));
  const previous = Object.fromEntries(
    ENVIRONMENT_KEYS.map((key) => [key, process.env[key]])
  );

  try {
    for (const key of ENVIRONMENT_KEYS) delete process.env[key];
    process.env.PLAYWRIGHT_BROWSERS_PATH = root;

    for (const revision of [999_998, 999_999]) {
      const executableDirectory = path.join(
        root,
        `chromium_headless_shell-${revision}`,
        "chrome-headless-shell-test"
      );
      await fs.mkdir(executableDirectory, { recursive: true });
      await fs.writeFile(
        path.join(executableDirectory, "chrome-headless-shell.exe"),
        "test"
      );
    }

    const resolved = await resolveAuthoredHybridChromeExecutable();
    assert.ok(resolved);
    assert.match(resolved, /chromium_headless_shell-999999/);
  } finally {
    for (const key of ENVIRONMENT_KEYS) {
      const value = previous[key];
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("resolver preserves explicit authored Chrome path precedence", async () => {
  const previous = Object.fromEntries(
    ENVIRONMENT_KEYS.map((key) => [key, process.env[key]])
  );

  try {
    process.env.AUTHORED_HYBRID_CHROME_PATH = "/runtime/authored-chrome";
    process.env.PUPPETEER_EXECUTABLE_PATH = "/runtime/puppeteer-chrome";
    process.env.CHROME_PATH = "/runtime/default-chrome";
    const resolved = await resolveAuthoredHybridChromeExecutable();
    assert.equal(resolved, "/runtime/authored-chrome");
  } finally {
    for (const key of ENVIRONMENT_KEYS) {
      const value = previous[key];
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});
