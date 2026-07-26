import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  extractAuthoredSlideDom,
  resolveAuthoredHybridChromeExecutable,
} from "./index.ts";
import { runAuthoredHybridChrome } from "./chrome-runner.ts";

const COMPLETE_ONE_PIXEL_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";

test("legacy static HTML is extracted without leaving Chrome temp artifacts", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") {
      assert.fail("CI must provide Chrome/Chromium for authored cleanup coverage");
    }
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const originalTemp = process.env.TEMP_DIRECTORY;
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "presenton-hybrid-cleanup-"));
  process.env.TEMP_DIRECTORY = root;
  try {
    const slide = await extractAuthoredSlideDom(
      `<!doctype html><meta charset="utf-8"><style>
        html,body{margin:0;width:1280px;height:720px;background:#fff}
        h1{position:absolute;left:80px;top:60px;font:700 44px Arial,sans-serif;color:#123}
      </style><h1 data-ppt-role="title">레거시 서울 HTML</h1>`,
      { chromeExecutable, timeoutMs: 30_000 }
    );
    assert.ok(
      slide.elements.some(
        (element) => "text" in element && element.text.plainText.includes("레거시 서울")
      )
    );
    assert.deepEqual(await fs.readdir(root), []);
  } finally {
    if (originalTemp === undefined) delete process.env.TEMP_DIRECTORY;
    else process.env.TEMP_DIRECTORY = originalTemp;
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("a timed-out capture still removes its Chrome work directory", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") {
      assert.fail("CI must provide Chrome/Chromium for authored cleanup coverage");
    }
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const originalTemp = process.env.TEMP_DIRECTORY;
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "presenton-hybrid-timeout-"));
  process.env.TEMP_DIRECTORY = root;
  try {
    // A tiny timeout forces the terminate path while Chrome is still launching and
    // (on Windows) holding a --user-data-dir lock. The work dir must be gone once
    // the rejected promise settles — the runner waits for the process to exit first.
    await assert.rejects(
      extractAuthoredSlideDom(
        `<!doctype html><meta charset="utf-8"><body>서울</body>`,
        { chromeExecutable, timeoutMs: 1 }
      ),
      /timed out/
    );
    assert.deepEqual(await fs.readdir(root), []);
  } finally {
    if (originalTemp === undefined) delete process.env.TEMP_DIRECTORY;
    else process.env.TEMP_DIRECTORY = originalTemp;
    await fs.rm(root, { recursive: true, force: true });
  }
});

test(
  "a completed screenshot is harvested when headless Chrome stays alive",
  { skip: process.platform === "win32" ? "POSIX headless-process regression" : false },
  async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "presenton-hybrid-hung-"));
  const fakeChromeScript = path.join(root, "fake-chrome.mjs");
  const fakeChromeExecutable = path.join(root, "fake-chrome");
  await fs.writeFile(
    fakeChromeScript,
    `import fs from "node:fs";
const output = process.argv.find((arg) => arg.startsWith("--screenshot="))?.slice(13);
if (!output) process.exit(2);
fs.writeFileSync(output, Buffer.from("${COMPLETE_ONE_PIXEL_PNG}", "base64"));
setInterval(() => {}, 1000);
`,
    "utf8"
  );
  await fs.writeFile(
    fakeChromeExecutable,
    `#!/bin/sh\nexec "${process.execPath}" "${fakeChromeScript}" "$@"\n`,
    { encoding: "utf8", mode: 0o755 }
  );

  const startedAt = Date.now();
  try {
    const capture = await runAuthoredHybridChrome({
      chromeExecutable: fakeChromeExecutable,
      timeoutMs: 5_000,
      dumpDom: false,
      screenshot: true,
      html: "<!doctype html><body>complete screenshot</body>",
    });
    assert.deepEqual(
      capture.screenshotPng,
      Buffer.from(COMPLETE_ONE_PIXEL_PNG, "base64")
    );
    assert.ok(
      Date.now() - startedAt < 3_000,
      "completed screenshot should return before the outer watchdog"
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
  }
);
