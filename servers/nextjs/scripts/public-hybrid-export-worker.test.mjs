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
  assert.equal(mapHybridSlideFallbackCode(new Error("customer text must not persist")), "unknown-exception");
});

test("an aggregate teardown failure is classified by the errors it carries", () => {
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
  for (const code of ["chrome-teardown-timeout", "chrome-teardown-failed"]) {
    assert.match(code, receiptCode, "a new code must survive the receipt projection");
  }
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
