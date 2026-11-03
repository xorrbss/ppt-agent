import { followExportToArtifact } from "./ppt-agent-export-follow.mjs";

export const DOCUMENT_CANVAS_CONTRACT_VERSION = "document-canvas/v1";
export const documentCanvasHeaders = () => ({
  "X-Document-Canvas-Contracts": DOCUMENT_CANVAS_CONTRACT_VERSION,
});

function buildBody(content, opts) {
  // Include optional fields ONLY when provided; rely on server defaults otherwise.
  const body = { content };

  if (opts.slidesProvided && opts.nSlides !== null) body.n_slides = opts.nSlides;
  if (opts.language) body.language = opts.language;
  if (opts.template) body.template = opts.template;
  // Omitted entirely (not just falsy) when not given: the server default is
  // svg, and sending an explicit value here would only matter if it differs
  // from that default.
  if (opts.strategy) body.strategy = opts.strategy;
  if (opts.commonHeader) body.common_header_mode = opts.commonHeader;
  if (opts.aspectRatio) body.aspect_ratio = opts.aspectRatio;
  if (opts.pageSize) {
    body.page_size = opts.pageSize;
    body.orientation = opts.orientation;
  }
  body.export_as = opts.exportAs;
  if (opts.instructions) body.instructions = opts.instructions;
  if (opts.tone) body.tone = opts.tone;
  if (opts.verbosity) body.verbosity = opts.verbosity;
  if (opts.webSearch) body.web_search = true;
  // include_title_slide defaults true server-side; only send when toggled off.
  if (opts.noTitle) body.include_title_slide = false;
  if (opts.toc) body.include_table_of_contents = true;
  if (opts.visionQa) body.vision_qa = true;
  // Preserve the authored request shape unless a style preset was explicitly given.
  if (opts.authoredStyle) body.authored_style = opts.authoredStyle;
  // Authored-mode brand tokens (ignored by the template path).
  if (opts.primaryColor) body.primary_color = opts.primaryColor;
  if (opts.fonts) body.fonts = opts.fonts;
  if (opts.wordmark) body.wordmark = opts.wordmark;

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

export async function generateOne(content, opts) {
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
        `HTTP 401 Unauthorized — ${detail}. Provide --user/--password (web/Docker) or run against a DISABLE_AUTH backend.`
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
      `HTTP 401 Unauthorized — ${detail}. Provide --user/--password (web/Docker) or run against a DISABLE_AUTH backend.`
    );
  }
  throw new Error(`${descr} failed HTTP ${res.status}: ${detail}`);
}

export async function generateOneAsync(content, opts) {
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
  // The server commits status "completed" before it annotates the task with the
  // chained export (presentation_generation_export_chain.py), so a poll can land
  // in between. When an export was requested and neither outcome is there yet,
  // keep polling briefly instead of reporting that no export was chained.
  const exportGraceMs = 60000;
  const exportPollMs = 1000;
  let exportGraceUntil = null;
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
        const exportPending =
          body.export_as && data.export_operation_id == null && data.export_error == null;
        if (exportPending) {
          if (exportGraceUntil === null) exportGraceUntil = Date.now() + exportGraceMs;
          if (Date.now() < exportGraceUntil && remaining() > 0) {
            await new Promise((r) => setTimeout(r, Math.min(exportPollMs, Math.max(1, remaining()))));
            continue;
          }
        }
        return data; // { presentation_id, path, edit_path, export_operation_id?, export_error? }
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

function originOf(base) {
  try {
    const u = new URL(base);
    return u.origin;
  } catch {
    return base.replace(/\/+$/, "");
  }
}

// The export is a durable workspace operation now, not a path in the response.
// scripts/lib/ppt-agent-export-follow.mjs owns the polling and the download;
// it borrows the transport helpers above so there is one auth/timeout policy.
export async function downloadExport(result, opts) {
  return followExportToArtifact(result, opts, {
    abortableFetch,
    authHeader,
    extractDetail,
    fetchWithTimeout,
    readJsonSafely,
    remaining: () => opts.deadline - Date.now(),
    throwForStatus,
  });
}

// ---------- orchestration ----------

export function reportResult(content, result, opts, savedTo) {
  // The edit URL targets the UI origin (--ui-base) when given; otherwise it falls
  // back to the API --base origin (in a local run they are the same).
  const origin = originOf(opts.uiBase || opts.base);
  const editUrl = result.edit_path
    ? `${origin}${result.edit_path.startsWith("/") ? "" : "/"}${result.edit_path}`
    : "(none)";
  console.log(`  topic:           ${content}`);
  console.log(`  presentation_id: ${result.presentation_id}`);
  console.log(`  edit URL:        ${editUrl}`);
  console.log(`  deck artifact:   ${result.path}`);
  console.log(
    `  export op:       ${result.export_operation_id || `(none: ${result.export_error || "not requested"})`}`
  );
  if (savedTo) console.log(`  downloaded to:   ${savedTo}`);
}
