import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { mkdtemp, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

import sharp from "sharp";

const execFileAsync = promisify(execFile);
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const runnerPath = path.join(
  repoRoot,
  "electron",
  "resources",
  "document-extraction",
  "liteparse_runner.mjs"
);

async function makeImage(extension) {
  const tempDir = await mkdtemp(path.join(os.tmpdir(), "liteparse-runner-"));
  const filePath = path.join(tempDir, `sample${extension}`);
  const image = sharp({
    create: {
      width: 64,
      height: 32,
      channels: 3,
      background: "white",
    },
  });
  if (extension === ".jpg") {
    await image.jpeg().toFile(filePath);
  } else {
    await image.png().toFile(filePath);
  }
  return { tempDir, filePath };
}

test("PNG and JPEG bypass LiteParse/ImageMagick when OCR is disabled", async (t) => {
  for (const extension of [".png", ".jpg"]) {
    await t.test(extension, async () => {
      const { tempDir, filePath } = await makeImage(extension);
      try {
        const { stdout } = await execFileAsync(
          process.execPath,
          [
            runnerPath,
            "--file",
            filePath,
            "--ocr-enabled",
            "false",
            "--python-bridge",
            "plain",
          ],
          {
            cwd: repoRoot,
            encoding: "utf8",
            // If this branch regresses to LiteParse's image-to-PDF conversion,
            // no ImageMagick executable can be discovered through PATH.
            env: { ...process.env, PATH: "", Path: "" },
          }
        );
        assert.equal(stdout, "");
      } finally {
        await rm(tempDir, { recursive: true, force: true });
      }
    });
  }
});

test("direct image OCR reuses the configured HTTP OCR API", async () => {
  const { tempDir, filePath } = await makeImage(".png");
  let requestBody = "";
  const server = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      requestBody = Buffer.concat(chunks).toString("utf8");
      response.writeHead(200, { "content-type": "application/json" });
      response.end(
        JSON.stringify({
          results: [
            { text: "direct", bbox: [0, 0, 10, 10], confidence: 0.99 },
            { text: "OCR", bbox: [11, 0, 20, 10], confidence: 0.98 },
          ],
        })
      );
    });
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    assert.ok(address && typeof address === "object");
    const { stdout } = await execFileAsync(
      process.execPath,
      [
        runnerPath,
        "--file",
        filePath,
        "--ocr-enabled",
        "true",
        "--ocr-language",
        "kor+eng",
        "--ocr-server-url",
        `http://127.0.0.1:${address.port}/ocr`,
        "--python-bridge",
        "plain",
      ],
      { cwd: repoRoot, encoding: "utf8" }
    );

    assert.equal(stdout, "direct OCR");
    assert.match(requestBody, /name="language"/);
    assert.match(requestBody, /kor\+eng/);
    assert.match(requestBody, /filename="image.png"/);
  } finally {
    server.close();
    await rm(tempDir, { recursive: true, force: true });
  }
});
