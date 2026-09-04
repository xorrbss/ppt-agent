// generation-benchmark-report.mjs — aggregate per-run E1 benchmark records
// into the summary the phase-1 exit measurement gates on, plus a markdown
// table for humans. Pure functions only (no fs/network) so they are unit
// testable against synthetic run records.
//
// No npm dependencies. Node 18+.

/** Nearest-rank percentile over a numeric array (not interpolated). */
export function percentile(values, p) {
  const sorted = values.filter((v) => typeof v === "number" && Number.isFinite(v)).sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  const rank = Math.ceil((p / 100) * sorted.length) - 1;
  const clamped = Math.min(Math.max(rank, 0), sorted.length - 1);
  return sorted[clamped];
}

function average(values) {
  const finite = values.filter((v) => typeof v === "number" && Number.isFinite(v));
  if (finite.length === 0) return null;
  return finite.reduce((sum, v) => sum + v, 0) / finite.length;
}

function bumpHistogram(histogram, key) {
  const label = String(key);
  histogram[label] = (histogram[label] || 0) + 1;
}

function fitLadderRung(entry) {
  if (typeof entry === "number") return entry;
  if (entry && typeof entry === "object") {
    if (typeof entry.rung === "number") return entry.rung;
    if (typeof entry.rung === "string") return entry.rung;
  }
  return null;
}

function normalizationLabel(entry) {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object") {
    return entry.id || entry.name || entry.kind || entry.type || "unknown";
  }
  return "unknown";
}

/**
 * Build the E1 summary from an array of per-run records (the same shape
 * written to runs.jsonl by generation-benchmark.mjs). `wallMillis` used for
 * the wall p50/p90 is the harness's own client-observed elapsed time
 * (submit -> terminal poll), matching how G4 ("wall time, 20-slide ordinary
 * deck, worker lane") is phrased in the design doc — the receipt's internal
 * `wallMillis` is a secondary, per-attempt figure recorded alongside it.
 */
export function computeSummary(runs, { fixture = null, profile = null } = {}) {
  const total = runs.length;
  const succeeded = runs.filter((r) => r.outcome === "succeeded");
  const successRate = total > 0 ? succeeded.length / total : null;

  const wallValues = runs.map((r) => r.measuredWallMillis).filter((v) => typeof v === "number");
  const wallP50Millis = percentile(wallValues, 50);
  const wallP90Millis = percentile(wallValues, 90);

  const inputTokens = succeeded.map((r) => r.tokens && r.tokens.inputTokens).filter((v) => v != null);
  const outputTokens = succeeded.map((r) => r.tokens && r.tokens.outputTokens).filter((v) => v != null);
  const tokensPerSuccessfulDeck =
    inputTokens.length > 0 || outputTokens.length > 0
      ? {
          inputTokensAvg: average(inputTokens),
          outputTokensAvg: average(outputTokens),
          decksMeasured: Math.max(inputTokens.length, outputTokens.length),
        }
      : null;

  const rungHistogram = {};
  let rungObservations = 0;
  for (const run of runs) {
    if (!Array.isArray(run.fitLadderTrail)) continue;
    for (const entry of run.fitLadderTrail) {
      const rung = fitLadderRung(entry);
      if (rung === null) continue;
      bumpHistogram(rungHistogram, rung);
      rungObservations += 1;
    }
  }

  const normalizationCounts = {};
  let normalizationObservations = 0;
  for (const run of runs) {
    if (!Array.isArray(run.normalizations)) continue;
    for (const entry of run.normalizations) {
      bumpHistogram(normalizationCounts, normalizationLabel(entry));
      normalizationObservations += 1;
    }
  }

  const qualityLoopVariantHistogram = {};
  for (const run of runs) {
    if (run.qualityLoopVariant) bumpHistogram(qualityLoopVariantHistogram, run.qualityLoopVariant);
  }

  const terminalErrorHistogram = {};
  for (const run of runs) {
    if (run.terminalError) bumpHistogram(terminalErrorHistogram, run.terminalError);
  }

  const fallbackCounts = runs
    .map((r) => r.fallbackElementIdsCount)
    .filter((v) => typeof v === "number");

  return {
    fixture,
    profile,
    runs: total,
    succeeded: succeeded.length,
    successRate,
    wall: {
      p50Seconds: wallP50Millis === null ? null : wallP50Millis / 1000,
      p90Seconds: wallP90Millis === null ? null : wallP90Millis / 1000,
      measuredRuns: wallValues.length,
    },
    tokensPerSuccessfulDeck,
    rungHistogram: rungObservations > 0 ? rungHistogram : null,
    normalizationCounts: normalizationObservations > 0 ? normalizationCounts : null,
    qualityLoopVariantHistogram:
      Object.keys(qualityLoopVariantHistogram).length > 0 ? qualityLoopVariantHistogram : null,
    terminalErrorHistogram:
      Object.keys(terminalErrorHistogram).length > 0 ? terminalErrorHistogram : null,
    fallbackElementIdsCountAvg: fallbackCounts.length > 0 ? average(fallbackCounts) : null,
    fallbackElementIdsMeasuredRuns: fallbackCounts.length,
  };
}

/**
 * Apply the phase-1 exit thresholds to a summary. Success rate gates on the
 * design doc's phase-1 target (>= 80%); wall time gates on p90 (the
 * worst-realistic-case reading, not the median) against the 20-slide/7-minute
 * target so a few slow tails don't get averaged away.
 */
export function evaluateThresholds(summary, { successMin = 0.8, wallMaxSeconds = 420 } = {}) {
  const reasons = [];
  const successRate = summary.successRate;
  const successPass = successRate !== null && successRate >= successMin;
  if (!successPass) {
    reasons.push(
      `success rate ${successRate === null ? "n/a" : (successRate * 100).toFixed(1) + "%"} ` +
        `below --success-min ${(successMin * 100).toFixed(1)}%`
    );
  }
  const wallP90 = summary.wall.p90Seconds;
  const wallPass = wallP90 !== null && wallP90 <= wallMaxSeconds;
  if (!wallPass) {
    reasons.push(
      `wall p90 ${wallP90 === null ? "n/a" : wallP90.toFixed(1) + "s"} ` +
        `exceeds --wall-max-seconds ${wallMaxSeconds}s`
    );
  }
  const passed = successPass && wallPass;
  return { passed, successPass, wallPass, reasons, thresholds: { successMin, wallMaxSeconds } };
}

function formatPercent(value) {
  return value === null ? "n/a" : `${(value * 100).toFixed(1)}%`;
}

function formatSeconds(value) {
  return value === null ? "n/a" : `${value.toFixed(1)}s`;
}

function formatHistogram(histogram) {
  if (!histogram) return "(none observed)";
  return Object.entries(histogram)
    .sort((a, b) => b[1] - a[1])
    .map(([key, count]) => `${key}: ${count}`)
    .join(", ");
}

/** Render the summary (plus its threshold verdict) as a markdown table. */
export function formatMarkdown(summary, verdict) {
  const lines = [
    "# E1 fixture benchmark summary",
    "",
    `- fixture: \`${summary.fixture ?? "(unknown)"}\``,
    `- profile: ${summary.profile ?? "(unset)"}`,
    `- runs: ${summary.runs} (succeeded: ${summary.succeeded})`,
    `- success rate: ${formatPercent(summary.successRate)} (threshold: ${formatPercent(
      verdict.thresholds.successMin
    )})`,
    `- wall p50 / p90: ${formatSeconds(summary.wall.p50Seconds)} / ${formatSeconds(
      summary.wall.p90Seconds
    )} (threshold on p90: ${verdict.thresholds.wallMaxSeconds}s, measured over ${
      summary.wall.measuredRuns
    } run(s))`,
    `- verdict: ${verdict.passed ? "PASS" : "FAIL"}${
      verdict.reasons.length ? " — " + verdict.reasons.join("; ") : ""
    }`,
    "",
    "## Tokens per successful deck",
    "",
    summary.tokensPerSuccessfulDeck
      ? `input avg: ${summary.tokensPerSuccessfulDeck.inputTokensAvg?.toFixed(0)}, ` +
        `output avg: ${summary.tokensPerSuccessfulDeck.outputTokensAvg?.toFixed(0)} ` +
        `(n=${summary.tokensPerSuccessfulDeck.decksMeasured})`
      : "(no successful deck had measured token usage)",
    "",
    "## Fit-ladder rung histogram",
    "",
    formatHistogram(summary.rungHistogram),
    "",
    "## Normalisations fired",
    "",
    formatHistogram(summary.normalizationCounts),
    "",
    "## Quality-loop variant",
    "",
    formatHistogram(summary.qualityLoopVariantHistogram),
    "",
    "## Terminal errors",
    "",
    formatHistogram(summary.terminalErrorHistogram),
    "",
    "## fallbackElementIds count",
    "",
    summary.fallbackElementIdsCountAvg === null
      ? "(not measured on any run)"
      : `avg ${summary.fallbackElementIdsCountAvg.toFixed(2)} per deck ` +
        `(measured on ${summary.fallbackElementIdsMeasuredRuns} run(s))`,
    "",
  ];
  return lines.join("\n");
}
