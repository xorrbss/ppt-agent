import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  describeHybridErrorChain,
  mapHybridSlideFallbackCode,
} from "./public-hybrid-export-worker.mjs";
import { assembleWithPerSlideIntegrityFallback } from "./lib/hybrid-slide-assembly.mjs";
import { prepareHybridSlideWithRecovery } from "./lib/hybrid-slide-fallback-code.mjs";

const WORKER = fileURLToPath(new URL("./public-hybrid-export-worker.mjs", import.meta.url));
const DIGEST = `sha256:${"a".repeat(64)}`;

test("slide preparation errors map to bounded stable fallback codes", () => {
  assert.equal(
    mapHybridSlideFallbackCode(new Error("authored_hybrid_paint_band_complex_ancestor")),
    "authored_hybrid_paint_band_complex_ancestor",
  );
  assert.equal(
    mapHybridSlideFallbackCode(
      Object.assign(new Error("wrapper text"), { code: "authored_semantic_qid_mismatch" }),
    ),
    "authored_semantic_qid_mismatch",
  );
  assert.equal(
    mapHybridSlideFallbackCode(Object.assign(new Error("slow"), { name: "TimeoutError" })),
    "measurement-timeout",
  );
  assert.equal(mapHybridSlideFallbackCode(new Error("unexpected qid owner conflict")), "qid-ownership-mismatch");
  assert.equal(mapHybridSlideFallbackCode(new Error("Protocol error: Target closed")), "browser-guard-rejected");
  assert.equal(
    mapHybridSlideFallbackCode(Object.assign(new Error("break mismatch"), { code: "text_break_integrity" })),
    "text_break_integrity",
  );
  assert.equal(mapHybridSlideFallbackCode(new Error("customer text must not persist")), "unknown-exception");
});

test("an aggregate teardown failure is classified by the errors it carries", () => {
  const captureAndCleanup = new AggregateError([
    new Error("Protocol error: Target closed"),
    new Error("Windows cleanup helper taskkill timed out after 10000ms."),
  ], "Chrome capture failed and process/work-directory cleanup also failed: Target closed");
  assert.equal(mapHybridSlideFallbackCode(captureAndCleanup), "browser-guard-rejected");
  assert.match(describeHybridErrorChain(captureAndCleanup), /taskkill/u);
  // The exact shape an isolated export produced on every slide: the outer
  // message names only the teardown, and the reason lives in `errors[]`.
  // Reading the outer message alone reported `unknown-exception`, which is a
  // code that explains nothing about a plain, named timeout.
  const teardownTimeout = new AggregateError(
    [
      new Error("Windows cleanup helper taskkill timed out after 10000ms."),
      new Error("Windows cleanup helper powershell.exe timed out after 10000ms."),
    ],
    "Windows Chrome could not be fully terminated.",
  );
  assert.equal(mapHybridSlideFallbackCode(teardownTimeout), "chrome-teardown-timeout");

  // A teardown that failed for a reason other than time is a distinct code, so
  // a residual-process refusal is never read as a slow host.
  const residual = new AggregateError(
    [new Error("Windows Chrome cleanup left profile-associated process IDs: 4242.")],
    "Windows Chrome cleanup could not remove every profile-associated process.",
  );
  assert.equal(mapHybridSlideFallbackCode(residual), "chrome-teardown-failed");

  // A cause chain hides a reason the same way an aggregate does.
  assert.equal(
    mapHybridSlideFallbackCode(
      new Error("capture failed", {
        cause: new Error("wrapper", { cause: new Error("authored_render_readiness_timeout") }),
      }),
    ),
    "authored_render_readiness_timeout",
  );

  // A measurement timeout must not be relabelled as teardown.
  assert.equal(
    mapHybridSlideFallbackCode(
      new Error("Chrome authored hybrid capture timed out after 45000ms."),
    ),
    "measurement-timeout",
  );

  // A declared-graph member still wins over the generic rules around it.
  assert.equal(
    mapHybridSlideFallbackCode(
      new AggregateError(
        [new Error("Windows cleanup helper taskkill timed out after 10000ms.")],
        "authored_hybrid_paint_band_qid_mismatch",
      ),
    ),
    "authored_hybrid_paint_band_qid_mismatch",
  );
});

test("page capture recovery retries transient failures once within budget admission", async () => {
  for (const message of ["Connection closed", "Session closed"]) {
    assert.equal(mapHybridSlideFallbackCode(new Error(message)), "browser-guard-rejected");
    let calls = 0;
    const receipt = {};
    await prepareHybridSlideWithRecovery(async () => {
      if (++calls === 1) throw new Error(message);
      return { layer: { slideNumber: 65 } };
    }, 200_000, receipt, () => 0);
    assert.equal(calls, 2);
    assert.equal(receipt.retryDisposition, "recovered");
  }
  let attempts = 0;
  const diagnostics = {};
  const result = await prepareHybridSlideWithRecovery(async () => {
    if (++attempts === 1) throw new Error("Protocol error: Target closed");
    return { layer: { slideNumber: 45 } };
  }, 200_000, diagnostics, () => 0);
  assert.equal(result.layer.slideNumber, 45);
  assert.equal(attempts, 2);
  assert.equal(diagnostics.retryDisposition, "recovered");
  for (const [message, deadline, disposition, expectedAttempts] of [
    ["Chrome capture timed out", 119_999, "budget-insufficient", 1],
    ["authored_semantic_qid_mismatch", 200_000, "non-transient", 1],
    ["Windows cleanup helper timed out", 200_000, "non-transient", 1],
    ["Chrome capture timed out", 200_000, "failed", 2],
  ]) {
    attempts = 0;
    const receipt = {};
    await assert.rejects(prepareHybridSlideWithRecovery(async () => {
      attempts++;
      throw new Error(message);
    }, deadline, receipt, () => 0));
    assert.equal(attempts, expectedAttempts);
    assert.equal(receipt.retryDisposition, disposition);
  }
});

test("the error chain walk is bounded and cycle safe", () => {
  const cyclic = new Error("outer");
  cyclic.cause = cyclic;
  assert.equal(mapHybridSlideFallbackCode(cyclic), "unknown-exception");
  assert.equal(describeHybridErrorChain(cyclic), "Error | outer");

  // A chain deeper than the bound must still classify by what it did read,
  // never hang or throw.
  let deep = new Error("authored_semantic_qid_invalid");
  for (let level = 0; level < 40; level += 1) {
    deep = new Error(`level-${level}`, { cause: deep });
  }
  assert.equal(mapHybridSlideFallbackCode(deep), "unknown-exception");

  const wide = new AggregateError(
    Array.from({ length: 500 }, (_, index) => new Error(`member-${index}`)),
    "Windows Chrome could not be fully terminated.",
  );
  assert.equal(mapHybridSlideFallbackCode(wide), "chrome-teardown-failed");

  // Nothing unbounded reaches the stderr tail either.
  const shouty = new Error("x".repeat(5_000));
  assert.ok(describeHybridErrorChain(shouty).length <= 1_000);

  assert.equal(describeHybridErrorChain("a thrown string"), "");
  assert.equal(mapHybridSlideFallbackCode(undefined), "unknown-exception");
});

test("the persisted per-slide code stays inside the bounded receipt shape", () => {
  const receiptCode = /^[a-z][a-z0-9_-]{0,95}$/u;
  for (const code of ["chrome-teardown-timeout", "chrome-teardown-failed", "text_break_integrity"]) {
    assert.match(code, receiptCode, "a new code must survive the receipt projection");
  }
});

test("a text-break integrity refusal falls back only its owning slide", async () => {
  const outcomes = [1, 2, 3].map((slideNumber) => ({
    layer: { slideNumber },
    quality: { slideNumber, imageFallback: false },
    fontRendering: { browserFontFilesCollected: 0, browserCollectionFailures: 0 },
  }));
  let attempts = 0;
  const assembled = await assembleWithPerSlideIntegrityFallback({
    basePptx: Buffer.from("base"),
    outcomes,
    assemblerOptions: {},
    assemble: async (_base, layers) => {
      attempts += 1;
      if (layers.some((layer) => layer.slideNumber === 2)) {
        throw Object.assign(new Error("Hybrid text q-phone-step-1 lost an authored line break."), {
          code: "text_break_integrity",
          slideNumber: 2,
        });
      }
      return Buffer.from("hybrid");
    },
    mapFallbackCode: mapHybridSlideFallbackCode,
    createFallbackOutcome: (slideNumber, code) => ({
      layer: null,
      quality: { slideNumber, imageFallback: true, fallbackReasons: [code] },
      fontRendering: { browserFontFilesCollected: 0, browserCollectionFailures: 0 },
    }),
  });
  assert.equal(attempts, 2);
  assert.deepEqual(assembled.layers.map((layer) => layer.slideNumber), [1, 3]);
  assert.equal(assembled.outcomes[0].quality.imageFallback, false);
  assert.deepEqual(assembled.outcomes[1].quality.fallbackReasons, ["text_break_integrity"]);
  assert.equal(assembled.outcomes[2].quality.imageFallback, false);
  assert.equal(assembled.hybridBytes.toString("utf8"), "hybrid");
});

/** Run the worker with one job and return its single result line. */
function runWorker(job) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [WORKER], {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      env: { ...process.env, AUTHORED_VNEXT_RENDERER_ENABLED: "false" },
    });
    const out = [];
    child.stdout.on("data", (chunk) => out.push(chunk));
    child.stderr.resume();
    child.once("error", reject);
    child.stdin.on("error", (error) => { if (error.code !== "EPIPE") reject(error); });
    child.once("close", () => {
      const text = Buffer.concat(out).toString("utf8").trim();
      const line = text.split("\n").filter(Boolean).at(-1);
      resolve(line ? JSON.parse(line) : null);
    });
    child.stdin.end(JSON.stringify(job));
  });
}

function baseJob(overrides = {}) {
  const scratch = mkdtempSync(path.join(tmpdir(), "hybrid-worker-test-"));
  const html = path.join(scratch, "slide.html");
  const base = path.join(scratch, "base.pptx");
  writeFileSync(html, "<html></html>", "utf8");
  writeFileSync(base, "not-a-real-package");
  return {
    schemaVersion: "public-hybrid-export-job/v1",
    basePptxPath: base,
    outputPath: path.join(scratch, "out.pptx"),
    scratchDirectory: scratch,
    fontEmbedding: false,
    slides: [{ slideIndex: 0, htmlPath: html }],
    ...overrides,
  };
}

test("a declared graph without its attempt and renderer identity is refused", async () => {
  const declared = {
    schemaVersion: "public-hybrid-declared-semantic/v1",
    slideIndex: 0,
    bundleDigest: DIGEST,
    fontSetDigest: DIGEST,
    declaredElementIds: ["q-a"],
    declaredElements: [{ elementId: "q-a", kind: "shape", sourceDigest: DIGEST }],
  };

  // The graph seals an observation receipt against a specific export attempt
  // and renderer runtime. Accepting it without both would let the assembler
  // publish a receipt that names nothing.
  const unbound = await runWorker(baseJob({ semanticSlides: [declared] }));
  assert.equal(unbound.ok, false);
  assert.equal(unbound.errorCode, "hybrid_job_semantic_binding_incomplete");

  const shortGraph = await runWorker(
    baseJob({
      semanticSlides: [],
      exportAttemptId: "attempt-1",
      rendererSourceDigest: DIGEST,
    }),
  );
  // An empty graph is simply the observed-only path, not an error; it fails
  // later for an unrelated reason (no Chrome / invalid base package).
  assert.notEqual(shortGraph.errorCode, "hybrid_job_semantic_binding_incomplete");
});

test("a malformed declared graph is refused rather than partially applied", async () => {
  for (const declared of [
    { slideIndex: 1, bundleDigest: DIGEST, fontSetDigest: DIGEST, declaredElementIds: [], declaredElements: [] },
    { slideIndex: 0, bundleDigest: "not-a-digest", fontSetDigest: DIGEST, declaredElementIds: [], declaredElements: [] },
    { slideIndex: 0, bundleDigest: DIGEST, fontSetDigest: DIGEST, declaredElements: [] },
  ]) {
    const result = await runWorker(
      baseJob({
        semanticSlides: [declared],
        exportAttemptId: "attempt-1",
        rendererSourceDigest: DIGEST,
      }),
    );
    assert.equal(result.ok, false);
    assert.equal(result.errorCode, "hybrid_job_semantic_invalid");
  }
});

test("the job wire rejects relative paths", async () => {
  const result = await runWorker(baseJob({ basePptxPath: "relative/base.pptx" }));
  assert.equal(result.ok, false);
  assert.equal(result.errorCode, "hybrid_job_path_not_absolute");
});

test("wire byte boundary admits equal size and refuses excess before execution", async () => {
  const limit = 8 * 1024 * 1024;
  const job = baseJob({ basePptxPath: "relative/base.pptx", padding: "한글" });
  const initial = Buffer.byteLength(JSON.stringify(job), "utf8");
  for (const delta of [-1, 0, 1]) {
    const result = await runWorker({ ...job, padding: job.padding + "x".repeat(limit - initial + delta) });
    assert.equal(result.errorCode, delta > 0 ? "hybrid_job_wire_limit" : "hybrid_job_path_not_absolute");
  }
});
