import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  extractAuthoredSlideDom,
  resolveAuthoredHybridChromeExecutable,
} from "./index.ts";
import {
  __testOnlyAssertWindowsProfileGone,
  __testOnlyRunWindowsHelper,
  runAuthoredHybridChrome,
} from "./chrome-runner.ts";
import { instrumentAuthoredHtml } from "./html-instrumentation.ts";

const COMPLETE_ONE_PIXEL_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";
const FIXTURE_COMPLETION_TOKEN = "authored-hybrid-fixture-complete";
const COMPLETION_MARKER_NAME = "__presenton_authored_hybrid_complete_v1__";

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
  "completed Chrome artifacts wait for the trusted marker, not a streamed closing tag",
  { skip: process.platform === "win32" ? "POSIX process-lifecycle regression" : false },
  async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "presenton-hybrid-hung-"));
  const fakeChromeScript = path.join(root, "fake-chrome.mjs");
  const fakeChromeExecutable = path.join(root, "fake-chrome");
  await fs.writeFile(
    fakeChromeScript,
    `import fs from "node:fs";
const output = process.argv.find((arg) => arg.startsWith("--screenshot="))?.slice(13);
if (output) fs.writeFileSync(output, Buffer.from("${COMPLETE_ONE_PIXEL_PNG}", "base64"));
process.stdout.write("<!doctype html><html><body>complete capture</body></ht");
setTimeout(() => process.stdout.write("ml>"), 15);
setTimeout(() => process.stdout.write('<meta name="${COMPLETION_MARKER_NAME}" content="${FIXTURE_COMPLETION_TOKEN}">'), 30);
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
      dumpDom: true,
      screenshot: true,
      domCompletionToken: FIXTURE_COMPLETION_TOKEN,
      html: "<!doctype html><body>complete screenshot</body>",
    });
    assert.match(capture.serializedDom ?? "", /complete capture/);
    assert.match(capture.serializedDom ?? "", new RegExp(FIXTURE_COMPLETION_TOKEN));
    assert.deepEqual(
      capture.screenshotPng,
      Buffer.from(COMPLETE_ONE_PIXEL_PNG, "base64")
    );
    assert.ok(
      Date.now() - startedAt < 3_000,
      "completed screenshot should return before the outer watchdog"
    );

    const domOnly = await runAuthoredHybridChrome({
      chromeExecutable: fakeChromeExecutable,
      timeoutMs: 5_000,
      dumpDom: true,
      screenshot: false,
      domCompletionToken: FIXTURE_COMPLETION_TOKEN,
      html: "<!doctype html><body>complete DOM</body>",
    });
    assert.match(domOnly.serializedDom ?? "", /complete capture/);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
  }
);

test(
  "Windows launcher handoff still removes the unique Chrome profile",
  { skip: process.platform === "win32" ? false : "Windows Chrome launcher regression" },
  async (t) => {
    const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
    if (!chromeExecutable) {
      if (process.env.CI === "true") {
        assert.fail("CI must provide Chrome/Chromium for authored cleanup coverage");
      }
      t.skip("Chrome/Chromium is unavailable");
      return;
    }

    const originalTemp = process.env.TEMP_DIRECTORY;
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "presenton-hybrid-launcher-"));
    const completionToken = "windows-launcher-handoff-token";
    process.env.TEMP_DIRECTORY = root;
    try {
      const capture = await runAuthoredHybridChrome({
        chromeExecutable,
        timeoutMs: 30_000,
        dumpDom: true,
        screenshot: true,
        domCompletionToken: completionToken,
        html: instrumentAuthoredHtml("<!doctype html><body>launcher handoff</body>", {
          completionToken,
        }),
      });
      assert.match(capture.serializedDom ?? "", /launcher handoff/);
      assert.ok(capture.screenshotPng?.length);
      assert.deepEqual(await fs.readdir(root), []);
    } finally {
      if (originalTemp === undefined) delete process.env.TEMP_DIRECTORY;
      else process.env.TEMP_DIRECTORY = originalTemp;
      await fs.rm(root, { recursive: true, force: true });
    }
  }
);

test(
  "Windows concurrent captures retain completed artifacts while cleaning Chrome profiles",
  { skip: process.platform === "win32" ? false : "Windows Chrome concurrency regression" },
  async (t) => {
    const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
    if (!chromeExecutable) {
      if (process.env.CI === "true") {
        assert.fail("CI must provide Chrome/Chromium for authored cleanup coverage");
      }
      t.skip("Chrome/Chromium is unavailable");
      return;
    }

    const originalTemp = process.env.TEMP_DIRECTORY;
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "presenton-hybrid-concurrent-"));
    process.env.TEMP_DIRECTORY = root;
    try {
      const captures = await Promise.all(
        Array.from({ length: 8 }, (_, index) => {
          const completionToken = `windows-concurrent-${process.pid}-${index}`;
          return runAuthoredHybridChrome({
            chromeExecutable,
            timeoutMs: 30_000,
            dumpDom: true,
            screenshot: true,
            domCompletionToken: completionToken,
            html: instrumentAuthoredHtml(
              `<!doctype html><body>concurrent capture ${index}</body>`,
              { completionToken }
            ),
          });
        })
      );

      captures.forEach((capture, index) => {
        assert.match(capture.serializedDom ?? "", new RegExp(`concurrent capture ${index}`));
        assert.ok(capture.screenshotPng?.length);
      });
      assert.deepEqual(await fs.readdir(root), []);
    } finally {
      if (originalTemp === undefined) delete process.env.TEMP_DIRECTORY;
      else process.env.TEMP_DIRECTORY = originalTemp;
      await fs.rm(root, { recursive: true, force: true });
    }
  }
);

test(
  "Windows cleanup helpers fail closed for timeout, nonzero exit, and residual profile processes",
  { skip: process.platform === "win32" ? false : "Windows cleanup helper regression" },
  async () => {
    await assert.rejects(
      __testOnlyRunWindowsHelper(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", "exit 7"],
        { timeoutMs: 10_000 }
      ),
      /exited with code 7/
    );

    const timeoutStartedAt = Date.now();
    await assert.rejects(
      __testOnlyRunWindowsHelper(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Seconds 10"],
        { timeoutMs: 100 }
      ),
      /timed out after 100ms/
    );
    assert.ok(
      Date.now() - timeoutStartedAt < 2_000,
      "a hung cleanup helper must be bounded and killed"
    );

    const profilePath = path.join(
      os.tmpdir(),
      `presenton hybrid residue ${Date.now()} ${process.pid}`
    );
    const stopResident = async (resident) => {
      if (resident.exitCode === null && resident.signalCode === null) {
        resident.kill();
      }
      if (resident.exitCode === null && resident.signalCode === null) {
        await new Promise((resolve) => resident.once("close", resolve));
      }
    };
    const prefixedResident = spawn(
      process.execPath,
      [
        "-e",
        "setTimeout(() => {}, 10_000)",
        "--",
        `--user-data-dir=${profilePath}-prefix`,
      ],
      { stdio: "ignore", windowsHide: true }
    );
    try {
      await new Promise((resolve) => setTimeout(resolve, 150));
      await __testOnlyAssertWindowsProfileGone(profilePath);
    } finally {
      await stopResident(prefixedResident);
    }

    const exactResident = spawn(
      process.execPath,
      [
        "-e",
        "setTimeout(() => {}, 10_000)",
        "--",
        `--user-data-dir=${profilePath}`,
      ],
      { stdio: "ignore", windowsHide: true }
    );
    try {
      await new Promise((resolve) => setTimeout(resolve, 150));
      await assert.rejects(
        __testOnlyAssertWindowsProfileGone(profilePath),
        /profile-associated process IDs/
      );
    } finally {
      await stopResident(exactResident);
    }
  }
);
