// Follow a chained export operation to its downloadable artifact.
//
// `POST /presentation/generate` no longer runs the exporter inside the request
// and no longer answers with a server-side file path. When the request carried
// `export_as`, the server submits an `export.pptx` / `export.pdf` workspace
// command and answers with its operation id. This module turns that id into a
// file on disk: poll the presentation timeline until the operation is terminal,
// then download the artifact it published.

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const WORKSPACE_ROOT = "/api/v1/ppt/workspace";

// Mirrors the workspace operation states the API projects. Anything outside
// this set is still in flight.
export const TERMINAL_OPERATION_STATES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "dead_letter",
]);

export function exportOperationIdOf(result) {
  const id = result && result.export_operation_id;
  return typeof id === "string" && id.trim() !== "" ? id.trim() : null;
}

/** Find one operation card in a projected timeline page. */
export function findOperationItem(timeline, operationId) {
  const items = (timeline && timeline.items) || [];
  for (const item of items) {
    if (item && item.anchor_type === "operation" && item.operation_id === operationId) {
      return item;
    }
  }
  return null;
}

/** The artifact id an export operation published, if it has one yet. */
export function artifactIdOf(item) {
  const result = (item && item.result) || {};
  const id = result.artifact_id ?? result.artifactId;
  return typeof id === "string" && id.trim() !== "" ? id.trim() : null;
}

export function describeOperationFailure(item) {
  const detail =
    (item && (item.error || item.error_message)) ||
    (item && item.result && item.result.failure_code) ||
    "(no detail)";
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}

/** Filename the server asked for, or a deterministic fallback. */
export function attachmentFilename(contentDisposition, fallback) {
  if (typeof contentDisposition === "string") {
    const quoted = /filename="([^"]+)"/u.exec(contentDisposition);
    if (quoted) return path.basename(quoted[1]);
    const bare = /filename=([^;]+)/u.exec(contentDisposition);
    if (bare) return path.basename(bare[1].trim());
  }
  return fallback;
}

/**
 * Poll the deck timeline until the chained export operation is terminal, then
 * download its artifact into `opts.outDir`.
 *
 * `deps` carries the CLI's own fetch/auth helpers so this module owns no
 * transport policy: { fetchWithTimeout, throwForStatus, authHeader, remaining }.
 */
export async function followExportToArtifact(result, opts, deps) {
  const operationId = exportOperationIdOf(result);
  if (!operationId) {
    const reason = result && result.export_error;
    throw new Error(
      reason
        ? `the server did not start the requested export (${reason})`
        : "the server returned no export_operation_id; the deck was generated but no export was chained"
    );
  }

  const headers = { Accept: "application/json", ...deps.authHeader(opts.user, opts.password) };
  const timelineUrl =
    `${opts.base}${WORKSPACE_ROOT}/presentations/` +
    `${encodeURIComponent(result.presentation_id)}/timeline`;

  let item = null;
  while (true) {
    const left = deps.remaining();
    if (left <= 0) {
      throw new Error(`export did not finish within ${opts.timeoutMs / 1000}s (--timeout)`);
    }
    const poll = await deps.fetchWithTimeout(
      timelineUrl,
      { method: "GET", headers },
      Math.max(1, left),
      "export status poll"
    );
    if (!poll.res.ok) {
      deps.throwForStatus(poll.res, poll.json, poll.text, "export status poll");
    }
    item = findOperationItem(poll.json, operationId);
    if (item && TERMINAL_OPERATION_STATES.has(item.status)) break;
    const wait = Math.min(opts.exportPollIntervalMs ?? 5000, Math.max(1, deps.remaining()));
    await new Promise((resolve) => setTimeout(resolve, wait));
  }

  if (item.status !== "succeeded") {
    throw new Error(`export operation ${item.status}: ${describeOperationFailure(item)}`);
  }
  const artifactId = artifactIdOf(item);
  if (!artifactId) {
    throw new Error("export operation succeeded but published no artifact id");
  }

  const downloadUrl = `${opts.base}${WORKSPACE_ROOT}/artifacts/${encodeURIComponent(artifactId)}/download`;
  const res = await deps.abortableFetch(
    downloadUrl,
    { method: "GET", headers: { ...deps.authHeader(opts.user, opts.password) } },
    Math.max(1, deps.remaining()),
    "download"
  );
  if (!res.ok) {
    const { json, text } = await deps.readJsonSafely(res);
    throw new Error(`download failed HTTP ${res.status}: ${deps.extractDetail(json, text)}`);
  }

  const filename = attachmentFilename(
    res.headers.get("content-disposition"),
    `presentation-${result.presentation_id}.${opts.exportAs}`
  );
  await mkdir(opts.outDir, { recursive: true });
  const dest = path.join(opts.outDir, filename);
  await writeFile(dest, Buffer.from(await res.arrayBuffer()));
  return dest;
}
