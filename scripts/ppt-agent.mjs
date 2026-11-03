#!/usr/bin/env node
// ppt-agent.mjs — zero-dependency CLI to drive Presenton presentation generation.
//
// Calls POST <base>/api/v1/ppt/presentation/generate (synchronous: blocks until
// generation finishes) and prints presentation_id, the editor URL, and the deck
// artifact path. When the request carried --export, the server chains an
// `export.pptx`/`export.pdf` workspace command; --out follows that operation to
// completion and downloads its artifact.
//
// The svg strategy (the server default) always takes the async route below,
// because the inline endpoint cannot run svg (it has no durable job).
// With --async (or svg) it instead POSTs to .../generate/async and polls
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
//   --template <name>        retained template id (default "authored")
//   --strategy <svg|authored> generation strategy (default svg, matches the server default)
//   --common-header <uniform|free>
//                            svg common-header mode (server default uniform: one designed header)
//   --style <id>             authored-mode style preset id (sent as authored_style)
//   --export <pptx|pdf>      export_as (default pptx)
//   --aspect-ratio <r>       deck canvas aspect_ratio: 16:9|9:16|9:13|1:1|21:9 (server default 16:9)
//   --page-size A4 --orientation <landscape|portrait>
//                            document canvas instead of --aspect-ratio (both flags required together)
//   --instructions <str>     extra generation instructions
//   --tone <str>             default|casual|professional|funny|educational|sales_pitch
//   --verbosity <str>        concise|standard|text-heavy
//   --web-search             enable web_search
//   --no-title               set include_title_slide=false (default true)
//   --toc                    set include_table_of_contents=true (default false)
//   --base <url>             backend (API) base URL (default http://127.0.0.1:8000)
//   --ui-base <url>          UI origin for the printed edit URL (default: --base origin).
//                            Useful for web/Docker where the UI origin differs from the API --base
//   --user <u> --password <p>  HTTP Basic creds for web/Docker (omit for DISABLE_AUTH)
//   --out <dir>              follow the chained export operation and save its file into <dir>
//   --async                  use the async generate endpoint and poll status to completion
//                            (always on for svg, which the synchronous endpoint refuses)
//   --timeout <sec>          per-request / overall poll timeout in seconds (default 600)
//   -h, --help               show this help
//
// Examples:
//   # DISABLE_AUTH=true backend — no credentials:
//   node scripts/ppt-agent.mjs --content "Quarterly sales review" --slides 10 --out ./out
//
//   # Web/Docker with HTTP Basic auth:
//   node scripts/ppt-agent.mjs --content "회사 소개" --user admin --password s3cret \
//     --base http://localhost:5000 --export pdf --out ./out
//
//   # Batch mode (sequential, continues on per-item failure):
//   node scripts/ppt-agent.mjs --batch topics.txt --template authored --out ./decks
//
//   # Authored mode with a style preset:
//   node scripts/ppt-agent.mjs --content "2026 AI strategy" --mode authored --style strategic-navy --async --out ./out

import { readFile } from "node:fs/promises";
import { downloadExport, generateOne, generateOneAsync, reportResult } from "./lib/ppt-agent-client.mjs";

const TONES = ["default", "casual", "professional", "funny", "educational", "sales_pitch"];
const VERBOSITIES = ["concise", "standard", "text-heavy"];
const EXPORTS = ["pptx", "pdf"];
const STRATEGIES = ["svg", "authored"];
const COMMON_HEADER_MODES = ["uniform", "free"];
// Mirror of GeneratePresentationRequest.aspect_ratio / page_size / orientation.
const ASPECT_RATIOS = ["16:9", "9:16", "9:13", "1:1", "21:9"];
const PAGE_SIZES = ["A4"];
const ORIENTATIONS = ["landscape", "portrait"];
// Mirror of the server constant MAX_NUMBER_OF_SLIDES (constants/presentation.py).
const MAX_SLIDES = 50;

// ---------- arg parsing ----------

function parseArgs(argv) {
  // Flags that take no value.
  const booleans = new Set(["web-search", "no-title", "toc", "async", "vision-qa", "help", "h"]);
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

// Same exit status as die(), but without tearing the process down mid-flight.
// After any HTTP work, process.exit() can abort on an open keep-alive socket
// (Node 24 on Windows asserts in libuv); letting the loop drain is equivalent
// and safe. Reserve die() for failures raised before the first request.
function fail(msg, code = 1) {
  console.error(`error: ${msg}`);
  process.exitCode = code;
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
      "  --template <name>       default authored",
      "  --strategy <svg|authored>  generation strategy, default svg (server default)",
      "  --common-header <uniform|free>  svg header mode (server default uniform)",
      "  --mode authored         model-authored bespoke-HTML deck (image PPTX/PDF)",
      "  --style <id>            authored mode: style preset id",
      "  --vision-qa             authored mode: self-correct flagged slides (slower)",
      "  --primary-color <hex>   authored mode: brand primary colour (e.g. #2563EB)",
      "  --fonts <family>        authored mode: brand font family (e.g. 'Noto Sans KR')",
      "  --wordmark <text>       authored mode: footer wordmark",
      "  --export <pptx|pdf>     default pptx",
      "  --aspect-ratio <r>      canvas " + ASPECT_RATIOS.join("|") + " (server default 16:9)",
      "  --page-size A4 --orientation <landscape|portrait>  A4 document canvas (not with --aspect-ratio)",
      "  --instructions <str>    extra instructions",
      "  --tone <str>            " + TONES.join("|"),
      "  --verbosity <str>       " + VERBOSITIES.join("|"),
      "  --web-search            enable web search",
      "  --no-title              include_title_slide=false (default true)",
      "  --toc                   include_table_of_contents=true (default false)",
      "  --base <url>            API base URL, default http://127.0.0.1:8000",
      "  --ui-base <url>         UI origin for edit URL (default: --base origin)",
      "  --user <u> --password <p>  HTTP Basic (omit for DISABLE_AUTH)",
      "  --out <dir>             save the chained export's file into <dir>",
      "  --async                 use async endpoint + poll status (always on for svg)",
      "  --timeout <sec>         per-request / overall poll timeout, default 600",
      "  -h, --help              this help",
    ].join("\n")
  );
  process.exit(0);
}

// ---------- request building ----------

// The server rejects aspect_ratio together with page_size/orientation, and a
// page_size without its orientation; fail the same way before any request.
function resolveCanvas(args) {
  const aspect = args["aspect-ratio"];
  const pageSize = args["page-size"];
  const orientation = args.orientation;
  if (aspect !== undefined && !ASPECT_RATIOS.includes(aspect)) {
    die(`--aspect-ratio must be one of ${ASPECT_RATIOS.join(", ")} (got "${aspect}")`);
  }
  if (pageSize === undefined && orientation === undefined) {
    return { aspectRatio: aspect || null, pageSize: null, orientation: null };
  }
  if (aspect !== undefined) {
    die(`use either --aspect-ratio or --page-size/--orientation, not both`);
  }
  if (pageSize === undefined || orientation === undefined) {
    die(`--page-size and --orientation must be provided together`);
  }
  if (!PAGE_SIZES.includes(pageSize)) {
    die(`--page-size must be one of ${PAGE_SIZES.join(", ")} (got "${pageSize}")`);
  }
  if (!ORIENTATIONS.includes(orientation)) {
    die(`--orientation must be one of ${ORIENTATIONS.join(", ")} (got "${orientation}")`);
  }
  return { aspectRatio: null, pageSize, orientation };
}

async function processTopic(content, opts) {
  opts.deadline = Date.now() + opts.timeoutMs;
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
  if (args.style !== undefined && !args.style) {
    die(`--style requires a style preset id`);
  }
  if (args.strategy !== undefined && !STRATEGIES.includes(args.strategy)) {
    die(`--strategy must be one of ${STRATEGIES.join(", ")} (got "${args.strategy}")`);
  }

  const commonHeader = args["common-header"];
  if (commonHeader !== undefined && !COMMON_HEADER_MODES.includes(commonHeader)) {
    die(`--common-header must be one of ${COMMON_HEADER_MODES.join(", ")} (got "${commonHeader}")`);
  }
  if (commonHeader !== undefined && args.strategy === "authored") {
    die(`--common-header applies only to the svg strategy`);
  }

  const canvas = resolveCanvas(args);

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
    die(`--user and --password must be provided together (or omit both for DISABLE_AUTH)`);
  }

  const opts = {
    base,
    uiBase,
    // The synchronous endpoint refuses svg (422 svg_generation_requires_async_route).
    asyncMode: Boolean(args.async) || args.strategy !== "authored",
    exportAs,
    nSlides,
    slidesProvided,
    language: args.language !== undefined ? args.language : "Korean (한국어)",
    // --mode authored and the default both select the model-authored bespoke-HTML
    // pipeline. A supplied template is retained for explicit supported templates.
    template: args.mode === "authored" ? "authored" : args.template || "authored",
    strategy: args.strategy || null,
    commonHeader: commonHeader || null,
    aspectRatio: canvas.aspectRatio,
    pageSize: canvas.pageSize,
    orientation: canvas.orientation,
    instructions: args.instructions || null,
    tone: args.tone || null,
    verbosity: args.verbosity || null,
    visionQa: Boolean(args["vision-qa"]),
    authoredStyle: args.style || null,
    primaryColor: args["primary-color"] || null,
    fonts: args["fonts"] || null,
    wordmark: args["wordmark"] || null,
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
    process.exitCode = failed > 0 ? 1 : 0;
    return;
  }

  if (!args.content) {
    die(`--content <text> is required (or use --batch <file>). Use --help for usage.`);
  }

  try {
    await processTopic(args.content, opts);
  } catch (err) {
    fail(err && err.message ? err.message : String(err));
  }
}

main().catch((err) => {
  fail(err && err.message ? err.message : String(err));
});
