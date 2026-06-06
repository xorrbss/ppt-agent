#!/usr/bin/env node
// ppt-agent.mjs — zero-dependency CLI to drive Presenton presentation generation.
//
// Calls POST <base>/api/v1/ppt/presentation/generate (synchronous: blocks until
// generation + export finish) and prints presentation_id, the editor URL, and the
// server-side export 'path'. Optionally downloads the produced file over the
// auth-protected GET <base>/app_data/exports/<basename> static mount.
//
// With --async it instead POSTs to .../generate/async and polls
// GET <base>/api/v1/ppt/presentation/status/<task-id> until the task completes
// (or fails), respecting --timeout and the progress heartbeat.
//
// Requires Node 18+ (uses global fetch). No npm dependencies.
//
// Usage:
//   node scripts/ppt-agent.mjs --content "<topic>" [options]
//   node scripts/ppt-agent.mjs --batch topics.txt [options]   # one topic per line
//
// Common options:
//   --content <text>         Prompt/content for the deck (required unless --batch)
//   --batch <file>           File with one topic per line; each line becomes one deck
//   --slides <n>             n_slides (default 8, max 50). Use --slides auto to let the model decide
//   --language <str>         language (default "Korean (한국어)")
//   --template <name>        template id (default "adaptive")
//   --export <pptx|pdf>      export_as (default pptx)
//   --instructions <str>     extra generation instructions
//   --tone <str>             default|casual|professional|funny|educational|sales_pitch
//   --verbosity <str>        concise|standard|text-heavy
//   --web-search             enable web_search
//   --no-title               set include_title_slide=false (default true)
//   --toc                    set include_table_of_contents=true (default false)
//   --base <url>             backend (API) base URL (default http://127.0.0.1:8000)
//   --ui-base <url>          UI origin for the printed edit URL (default: --base origin).
//                            Useful for web/Docker where the UI origin differs from the API --base
//   --user <u> --password <p>  HTTP Basic creds for web/Docker (omit for Electron DISABLE_AUTH)
//   --out <dir>              download the produced file into <dir>
//   --async                  use the async generate endpoint and poll status to completion
//   --timeout <sec>          per-request / overall poll timeout in seconds (default 600)
//   -h, --help               show this help
//
// Examples:
//   # Electron (DISABLE_AUTH=true) — no credentials:
//   node scripts/ppt-agent.mjs --content "Quarterly sales review" --slides 10 --out ./out
//
//   # Web/Docker with HTTP Basic auth:
//   node scripts/ppt-agent.mjs --content "회사 소개" --user admin --password s3cret \
//     --base http://localhost:5000 --export pdf --out ./out
//
//   # Batch mode (sequential, continues on per-item failure):
//   node scripts/ppt-agent.mjs --batch topics.txt --template general --out ./decks

import { writeFile, mkdir, readFile } from "node:fs/promises";
import path from "node:path";

const TONES = ["default", "casual", "professional", "funny", "educational", "sales_pitch"];
const VERBOSITIES = ["concise", "standard", "text-heavy"];
const EXPORTS = ["pptx", "pdf"];
// Mirror of the server constant MAX_NUMBER_OF_SLIDES (constants/presentation.py).
const MAX_SLIDES = 50;

// ---------- arg parsing ----------

function parseArgs(argv) {
  // Flags that take no value.
  const booleans = new Set(["web-search", "no-title", "toc", "async", "help", "h"]);
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (!tok.startsWith("--") && !(tok === "-h")) {
      out._.push(tok);
      continue;
    }
    const raw = tok.replace(/^--?/, "");
    // Support `--flag=value` (value may itself start with `--`, contain spaces, etc.).
    const eq = raw.indexOf("=");
    if (eq !== -1) {
      out[raw.slice(0, eq)] = raw.slice(eq + 1);
      continue;
    }
    const key = raw;
    if (booleans.has(key)) {
      out[key] = true;
      continue;
    }
    const next = argv[i + 1];
    // A bare flag with no following token gets "" (surfaces as an explicit error
    // later). A following token that itself looks like a flag is NOT consumed —
    // use the `--flag=value` form to pass values that begin with `--`.
    if (next === undefined || next.startsWith("--")) {
      out[key] = "";
    } else {
      out[key] = next;
      i++;
    }
  }
  return out;
}

function die(msg, code = 1) {
  console.error(`error: ${msg}`);
  process.exit(code);
}

function printHelpAndExit() {
  // The top-of-file comment is the canonical help; echo a compact form here.
  console.log(
    [
      "ppt-agent — generate Presenton decks via the FastAPI backend.",
      "",
      'Usage: node scripts/ppt-agent.mjs --content "<topic>" [options]',
      "       node scripts/ppt-agent.mjs --batch topics.txt [options]",
      "",
      "Options:",
      "  --content <text>        prompt/content (required unless --batch)",
      "  --batch <file>          one topic per line",
      "  --slides <n|auto>       n_slides (default 8, max 50)",
      '  --language <str>        default "Korean (한국어)"',
      "  --template <name>       default adaptive",
      "  --export <pptx|pdf>     default pptx",
      "  --instructions <str>    extra instructions",
      "  --tone <str>            " + TONES.join("|"),
      "  --verbosity <str>       " + VERBOSITIES.join("|"),
      "  --web-search            enable web search",
      "  --no-title              include_title_slide=false (default true)",
      "  --toc                   include_table_of_contents=true (default false)",
      "  --base <url>            API base URL, default http://127.0.0.1:8000",
      "  --ui-base <url>         UI origin for edit URL (default: --base origin)",
      "  --user <u> --password <p>  HTTP Basic (omit for Electron)",
      "  --out <dir>             download produced file into <dir>",
      "  --async                 use async endpoint + poll status",
      "  --timeout <sec>         per-request / overall poll timeout, default 600",
      "  -h, --help              this help",
    ].join("\n")
  );
  process.exit(0);
}

// ---------- request building ----------

function buildBody(content, opts) {
  // Include optional fields ONLY when provided; rely on server defaults otherwise.
  const body = { content };

  if (opts.slidesProvided && opts.nSlides !== null) body.n_slides = opts.nSlides;
  if (opts.language) body.language = opts.language;
  if (opts.template) body.template = opts.template;
  body.export_as = opts.exportAs;
  if (opts.instructions) body.instructions = opts.instructions;
  if (opts.tone) body.tone = opts.tone;
  if (opts.verbosity) body.verbosity = opts.verbosity;
  if (opts.webSearch) body.web_search = true;
  // include_title_slide defaults true server-side; only send when toggled off.
  if (opts.noTitle) body.include_title_slide = false;
  if (opts.toc) body.include_table_of_contents = true;

  return body;
}

function authHeader(user, password) {
  if (user && password) {
    const token = Buffer.from(`${user}:${password}`, "utf8").toString("base64");
    return { Authorization: `Basic ${token}` };
  }
  return {};
}

async function readJsonSafely(res) {
  const text = await res.text();
  try {
    return { json: JSON.parse(text), text };
  } catch {
    return { json: null, text };
  }
}

function extractDetail(json, text) {
  if (json && typeof json === "object") {
    if (typeof json.detail === "string") return json.detail;
    if (json.detail) return JSON.stringify(json.detail);
    if (json.setup_required) return "Login setup is required (setup_required)";
  }
  const t = (text || "").trim();
  return t.length ? t.slice(0, 500) : "(no response body)";
}

// One fetch with an AbortController timeout. Translates abort/network failures
// into friendly Errors and returns the raw Response. `descr` names the operation
// for error messages; `timeoutMs` bounds this single call.
async function abortableFetch(url, init, timeoutMs, descr) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new Error(`${descr} timed out after ${Math.round(timeoutMs / 1000)}s (--timeout)`);
    }
    throw new Error(`network error reaching ${url}: ${err && err.message ? err.message : err}`);
  } finally {
    clearTimeout(timer);
  }
}

// ---------- progress heartbeat ----------

// Prints an elapsed-seconds line to stderr every ~15s while a long request is in
// flight, then clears it. Independent of the AbortController timeout. Returns a
// stop() that clears the interval and erases the heartbeat line (TTY only).
function startHeartbeat(label = "generating") {
  const startedAt = Date.now();
  const isTty = Boolean(process.stderr.isTTY);
  const emit = () => {
    const elapsed = Math.round((Date.now() - startedAt) / 1000);
    const line = `  …${label} (${elapsed}s elapsed)`;
    if (isTty) {
      process.stderr.write(`\r${line}`);
    } else {
      process.stderr.write(`${line}\n`);
    }
  };
  const timer = setInterval(emit, 15000);
  // Don't let the heartbeat keep the event loop alive on its own.
  if (typeof timer.unref === "function") timer.unref();
  return function stop() {
    clearInterval(timer);
    if (isTty) {
      // Erase the heartbeat line so it doesn't clutter final output.
      process.stderr.write("\r\x1b[K");
    }
  };
}

// ---------- core generate ----------

async function generateOne(content, opts) {
  const url = `${opts.base}/api/v1/ppt/presentation/generate`;
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...authHeader(opts.user, opts.password),
  };
  const body = buildBody(content, opts);

  const stopHeartbeat = startHeartbeat("generating");
  let res;
  try {
    res = await abortableFetch(
      url,
      { method: "POST", headers, body: JSON.stringify(body) },
      opts.timeoutMs,
      "generate request"
    );
  } finally {
    stopHeartbeat();
  }

  const { json, text } = await readJsonSafely(res);

  if (!res.ok) {
    const detail = extractDetail(json, text);
    if (res.status === 428) {
      throw new Error(`HTTP 428: server needs first-time login setup — ${detail}`);
    }
    if (res.status === 401) {
      throw new Error(
        `HTTP 401 Unauthorized — ${detail}. Provide --user/--password (web/Docker) or run against an Electron DISABLE_AUTH backend.`
      );
    }
    throw new Error(`HTTP ${res.status}: ${detail}`);
  }

  if (!json || !json.presentation_id) {
    throw new Error(`unexpected response (no presentation_id): ${(text || "").slice(0, 500)}`);
  }
  return json; // { presentation_id, path, edit_path }
}

// ---------- async generate (POST /generate/async + poll GET /status/{id}) ----------

// One-shot fetch with a per-call timeout bounded by `remainingMs` (the overall
// --timeout budget). Returns { res, json, text }.
async function fetchWithTimeout(url, init, remainingMs, descr) {
  const res = await abortableFetch(url, init, remainingMs, descr);
  const { json, text } = await readJsonSafely(res);
  return { res, json, text };
}

function throwForStatus(res, json, text, descr) {
  const detail = extractDetail(json, text);
  if (res.status === 428) {
    throw new Error(`HTTP 428: server needs first-time login setup — ${detail}`);
  }
  if (res.status === 401) {
    throw new Error(
      `HTTP 401 Unauthorized — ${detail}. Provide --user/--password (web/Docker) or run against an Electron DISABLE_AUTH backend.`
    );
  }
  throw new Error(`${descr} failed HTTP ${res.status}: ${detail}`);
}

async function generateOneAsync(content, opts) {
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...authHeader(opts.user, opts.password),
  };
  const body = buildBody(content, opts);
  const deadline = Date.now() + opts.timeoutMs;
  const remaining = () => deadline - Date.now();

  // 1) Kick off the async task.
  const postUrl = `${opts.base}/api/v1/ppt/presentation/generate/async`;
  const started = await fetchWithTimeout(
    postUrl,
    { method: "POST", headers, body: JSON.stringify(body) },
    Math.max(1, remaining()),
    "async generate request"
  );
  if (!started.res.ok) {
    throwForStatus(started.res, started.json, started.text, "async generate request");
  }
  const taskId = started.json && started.json.id;
  if (!taskId) {
    throw new Error(
      `unexpected async response (no task id): ${(started.text || "").slice(0, 500)}`
    );
  }

  // 2) Poll status until completed/error or the --timeout budget runs out.
  const statusUrl = `${opts.base}/api/v1/ppt/presentation/status/${encodeURIComponent(taskId)}`;
  const pollIntervalMs = 5000;
  const stopHeartbeat = startHeartbeat("generating (async)");
  try {
    while (true) {
      const left = remaining();
      if (left <= 0) {
        throw new Error(`async generation timed out after ${opts.timeoutMs / 1000}s (--timeout)`);
      }
      const poll = await fetchWithTimeout(
        statusUrl,
        { method: "GET", headers: { Accept: "application/json", ...authHeader(opts.user, opts.password) } },
        Math.max(1, left),
        "status poll"
      );
      if (!poll.res.ok) {
        throwForStatus(poll.res, poll.json, poll.text, "status poll");
      }
      const task = poll.json || {};
      const status = task.status;
      if (status === "completed") {
        const data = task.data;
        if (!data || !data.presentation_id) {
          throw new Error(
            `async task completed but had no result data: ${(poll.text || "").slice(0, 500)}`
          );
        }
        return data; // { presentation_id, path, edit_path }
      }
      if (status === "error") {
        const errDetail =
          task.error && (task.error.detail || JSON.stringify(task.error))
            ? task.error.detail || JSON.stringify(task.error)
            : task.message || "(no detail)";
        throw new Error(`async generation failed: ${errDetail}`);
      }
      // pending / in-progress: wait, but never sleep past the deadline.
      const wait = Math.min(pollIntervalMs, Math.max(1, remaining()));
      await new Promise((r) => setTimeout(r, wait));
    }
  } finally {
    stopHeartbeat();
  }
}

// ---------- download ----------

function baseName(p) {
  // result.path is a SERVER-side filesystem path; the server may be Windows
  // (backslashes) or POSIX. Split on both separators so the client OS variant
  // of path.basename() can't mangle it.
  const parts = String(p).split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : String(p);
}

function originOf(base) {
  try {
    const u = new URL(base);
    return u.origin;
  } catch {
    return base.replace(/\/+$/, "");
  }
}

async function downloadExport(result, opts) {
  // The API 'path' is a server-side filesystem path; the downloadable URL is the
  // basename mounted under /app_data/exports/ (auth-protected, same creds).
  if (typeof result.path !== "string" || result.path.trim() === "") {
    throw new Error(
      `cannot download: server response had no 'path' (got ${JSON.stringify(result.path)})`
    );
  }
  const basename = baseName(result.path);
  const url = `${opts.base}/app_data/exports/${encodeURIComponent(basename)}`;

  const res = await abortableFetch(
    url,
    { method: "GET", headers: { ...authHeader(opts.user, opts.password) } },
    opts.timeoutMs,
    "download"
  );

  if (!res.ok) {
    const { json, text } = await readJsonSafely(res);
    throw new Error(`download failed HTTP ${res.status}: ${extractDetail(json, text)}`);
  }

  await mkdir(opts.outDir, { recursive: true });
  const dest = path.join(opts.outDir, basename);
  const buf = Buffer.from(await res.arrayBuffer());
  await writeFile(dest, buf);
  return dest;
}

// ---------- orchestration ----------

function reportResult(content, result, opts, savedTo) {
  // The edit URL targets the UI origin (--ui-base) when given; otherwise it falls
  // back to the API --base origin (in Electron/local they are the same).
  const origin = originOf(opts.uiBase || opts.base);
  const editUrl = result.edit_path
    ? `${origin}${result.edit_path.startsWith("/") ? "" : "/"}${result.edit_path}`
    : "(none)";
  console.log(`  topic:           ${content}`);
  console.log(`  presentation_id: ${result.presentation_id}`);
  console.log(`  edit URL:        ${editUrl}`);
  console.log(`  export path:     ${result.path}`);
  if (savedTo) console.log(`  downloaded to:   ${savedTo}`);
}

async function processTopic(content, opts) {
  const result = opts.asyncMode
    ? await generateOneAsync(content, opts)
    : await generateOne(content, opts);
  let savedTo = null;
  if (opts.outDir) {
    savedTo = await downloadExport(result, opts);
  }
  reportResult(content, result, opts, savedTo);
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || args.h) printHelpAndExit();

  // --- validate / normalize options ---
  const base = (args.base || "http://127.0.0.1:8000").replace(/\/+$/, "");
  // --ui-base is optional; when given its origin is used for the printed edit URL.
  const uiBase = args["ui-base"] ? args["ui-base"].replace(/\/+$/, "") : null;
  if (args["ui-base"] !== undefined && !uiBase) {
    die(`--ui-base requires a URL value`);
  }

  const exportAs = args.export || "pptx";
  if (!EXPORTS.includes(exportAs)) {
    die(`--export must be one of ${EXPORTS.join(", ")} (got "${exportAs}")`);
  }

  if (args.tone !== undefined && !TONES.includes(args.tone)) {
    die(`--tone must be one of ${TONES.join(", ")} (got "${args.tone}")`);
  }
  if (args.verbosity !== undefined && !VERBOSITIES.includes(args.verbosity)) {
    die(`--verbosity must be one of ${VERBOSITIES.join(", ")} (got "${args.verbosity}")`);
  }

  // slides: default 8; "auto" (or empty) => omit n_slides so the model decides.
  let nSlides = 8;
  let slidesProvided = true;
  if (args.slides !== undefined) {
    if (args.slides === "" || String(args.slides).toLowerCase() === "auto") {
      slidesProvided = false;
      nSlides = null;
    } else {
      const n = Number(args.slides);
      if (!Number.isInteger(n) || n <= 0) {
        die(`--slides must be a positive integer or "auto" (got "${args.slides}")`);
      }
      // Client-side guard matching the server cap (MAX_NUMBER_OF_SLIDES=50) so we
      // fail fast before any network call.
      if (n > MAX_SLIDES) {
        die(`--slides cannot exceed ${MAX_SLIDES} (server cap MAX_NUMBER_OF_SLIDES=${MAX_SLIDES}); got ${n}`);
      }
      nSlides = n;
    }
  }

  let timeoutSec = 600;
  if (args.timeout !== undefined) {
    const t = Number(args.timeout);
    if (!Number.isFinite(t) || t <= 0) die(`--timeout must be a positive number of seconds`);
    timeoutSec = t;
  }

  // Auth: both --user and --password required together for Basic.
  if ((args.user && !args.password) || (!args.user && args.password)) {
    die(`--user and --password must be provided together (or omit both for Electron DISABLE_AUTH)`);
  }

  const opts = {
    base,
    uiBase,
    asyncMode: Boolean(args.async),
    exportAs,
    nSlides,
    slidesProvided,
    language: args.language !== undefined ? args.language : "Korean (한국어)",
    template: args.template || "adaptive",
    instructions: args.instructions || null,
    tone: args.tone || null,
    verbosity: args.verbosity || null,
    webSearch: Boolean(args["web-search"]),
    noTitle: Boolean(args["no-title"]),
    toc: Boolean(args.toc),
    user: args.user || null,
    password: args.password || null,
    outDir: args.out || null,
    timeoutMs: timeoutSec * 1000,
  };

  // --- determine topics ---
  if (args.content && args.batch) {
    die(`use either --content or --batch, not both`);
  }

  if (args.batch) {
    let raw;
    try {
      raw = await readFile(args.batch, "utf8");
    } catch (err) {
      die(`cannot read --batch file "${args.batch}": ${err && err.message ? err.message : err}`);
    }
    const topics = raw
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l.length > 0 && !l.startsWith("#"));
    if (topics.length === 0) die(`--batch file "${args.batch}" contained no topics`);

    let ok = 0;
    let failed = 0;
    console.log(`Processing ${topics.length} topic(s) against ${base} ...\n`);
    for (let i = 0; i < topics.length; i++) {
      const t = topics[i];
      console.log(`[${i + 1}/${topics.length}] ${t}`);
      try {
        await processTopic(t, opts);
        ok++;
      } catch (err) {
        failed++;
        console.error(`  FAILED: ${err && err.message ? err.message : err}`);
      }
      console.log("");
    }
    console.log(`Summary: ${ok} ok, ${failed} failed (of ${topics.length}).`);
    process.exit(failed > 0 ? 1 : 0);
  }

  if (!args.content) {
    die(`--content <text> is required (or use --batch <file>). Use --help for usage.`);
  }

  try {
    await processTopic(args.content, opts);
  } catch (err) {
    die(err && err.message ? err.message : String(err));
  }
}

main().catch((err) => {
  die(err && err.message ? err.message : String(err));
});
