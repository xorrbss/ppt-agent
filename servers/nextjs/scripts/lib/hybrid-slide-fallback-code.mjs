/**
 * Receipt-safe classification of a per-slide authored-hybrid failure.
 *
 * The isolated export lane persists a bounded code per slide and nothing else,
 * so this is the only place a caller's exception becomes something a receipt
 * can say. It must read the whole error, not just its outermost message: an
 * `AggregateError` states only that a group of operations failed, and a `cause`
 * chain hides its reason the same way. Reading the outer message alone reported
 * `unknown-exception` for every slide of an isolated export whose members named
 * a plain, bounded timeout.
 */

const MAX_ERROR_CHAIN_DEPTH = 3;
const MAX_ERROR_CHAIN_NODES = 12;
const MAX_ERROR_FIELD_LENGTH = 300;
const MAX_ERROR_CHAIN_TEXT = 1_000;

export const AUTHORED_HYBRID_ERROR_CODES = Object.freeze([
  "authored_fidelity_backplate_identity_mismatch", "authored_hybrid_chrome_unavailable",
  "authored_hybrid_export_attempt_binding_required", "authored_hybrid_isolated_raster_fallback_failed",
  "authored_hybrid_paint_band_complex_ancestor", "authored_hybrid_paint_band_qid_duplicate",
  "authored_hybrid_paint_band_qid_mismatch", "authored_hybrid_presentation_lookup_failed",
  "authored_hybrid_qid_owner_exact_once_mismatch", "authored_hybrid_requires_authored_presentation",
  "authored_hybrid_revision_invalid", "authored_hybrid_slide_count_unsupported",
  "authored_hybrid_slide_layer_missing", "authored_render_mutation_window_unstable",
  "authored_render_readiness_timeout", "authored_render_resources_unresolved",
  "authored_semantic_qid_duplicate", "authored_semantic_qid_invalid",
  "authored_semantic_qid_mismatch", "cached_hybrid_artifact_digest_mismatch",
  "stale_render_revision", "text_break_integrity",
]);

/** Chrome teardown, as distinct from the measurement it follows. */
const CHROME_TEARDOWN_PATTERN =
  /windows cleanup helper|windows chrome could not be fully terminated|windows chrome cleanup|profile-associated process/iu;
const TIMEOUT_PATTERN = /timeout|timed out/iu;

/**
 * Collect the classifiable text of an error and of the errors it carries.
 *
 * Depth, node count and field length stay bounded so a deep, wide or cyclic
 * chain cannot cost the export its receipt.
 */
function collectErrorFields(error) {
  const fields = [];
  const seen = new Set();
  const queue = [{ value: error, depth: 0 }];
  while (queue.length > 0 && seen.size < MAX_ERROR_CHAIN_NODES) {
    const { value, depth } = queue.shift();
    if (value === null || typeof value !== "object" || seen.has(value)) continue;
    seen.add(value);
    for (const field of [
      value.code,
      value.name,
      value instanceof Error ? value.message : null,
    ]) {
      if (typeof field === "string" && field !== "") {
        fields.push(field.slice(0, MAX_ERROR_FIELD_LENGTH));
      }
    }
    if (depth >= MAX_ERROR_CHAIN_DEPTH) continue;
    const members = Array.isArray(value.errors)
      ? value.errors.slice(0, MAX_ERROR_CHAIN_NODES)
      : [];
    for (const member of members) queue.push({ value: member, depth: depth + 1 });
    if (value.cause !== null && value.cause !== undefined) {
      queue.push({ value: value.cause, depth: depth + 1 });
    }
  }
  return fields;
}

/** Bounded rendering of an error chain for the worker's stderr tail. */
export function describeHybridErrorChain(error) {
  return collectErrorFields(error).join(" | ").slice(0, MAX_ERROR_CHAIN_TEXT);
}

/** Reduce an exception to a receipt-safe code without retaining its message. */
export function mapHybridSlideFallbackCode(error) {
  const fields = collectErrorFields(error);
  for (const code of AUTHORED_HYBRID_ERROR_CODES) {
    if (fields.some((value) => value === code || value.includes(code))) return code;
  }
  const description = fields.join(" ");
  // Teardown is decided ahead of the generic timeout rule. A cleanup helper
  // that ran out of time did not fail the measurement it followed, and calling
  // that `measurement-timeout` would send the next reader to the wrong
  // boundary entirely.
  if (CHROME_TEARDOWN_PATTERN.test(description)) {
    return TIMEOUT_PATTERN.test(description)
      ? "chrome-teardown-timeout"
      : "chrome-teardown-failed";
  }
  if (TIMEOUT_PATTERN.test(description)) return "measurement-timeout";
  if (/qid|semantic owner|ownership/iu.test(description)) return "qid-ownership-mismatch";
  if (/browser|protocol error|target closed|page crashed|execution context/iu.test(description)) {
    return "browser-guard-rejected";
  }
  return "unknown-exception";
}
