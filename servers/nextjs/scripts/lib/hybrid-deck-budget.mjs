/**
 * Deck-budget aware scheduling for the isolated hybrid export worker.
 *
 * The previous rule was `Date.now() >= deadline`: a slide was started whenever
 * any budget remained, even a millisecond of it. Because one slide costs tens
 * of seconds, that rule spent the tail of the budget on work that could not
 * finish, and every slide the pumps never reached became
 * `hybrid-deck-budget-exhausted` — an ordered suffix of raster slides as the
 * default failure mode (12 of 35 in the run-11 product-lane export).
 *
 * This scheduler refuses to *start* what it cannot finish: once enough slides
 * have been observed to estimate their cost, a slide is only begun when the
 * estimate fits in the remaining budget. Work already in flight is never
 * cancelled, so near the deadline the deck finishes slides instead of starting
 * new ones.
 *
 * It deliberately does not reorder the deck. Reordering would not increase how
 * many slides fit — that is throughput-bound, not order-bound — and it would
 * make *which* slides come back editable arbitrary from one run to the next.
 * The suffix stays the loser, but it is now a suffix the budget genuinely
 * could not cover.
 */

/** Samples required before an estimate is trusted enough to skip a slide. */
const MINIMUM_SAMPLES = 3;
/** Estimates are per-slide medians; this covers ordinary variance around one. */
const DEFAULT_SAFETY_FACTOR = 1.15;

/** Median of the observed durations, which ignores a single slow outlier. */
export function medianMillis(samples) {
  if (samples.length === 0) return null;
  const ordered = [...samples].sort((left, right) => left - right);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 1
    ? ordered[middle]
    : Math.round((ordered[middle - 1] + ordered[middle]) / 2);
}

/**
 * Map `items` with bounded concurrency inside a deck budget.
 *
 * `callback` runs one item; `onExpired` produces the result for an item the
 * budget cannot cover. Returns the outputs plus telemetry describing how the
 * budget was spent, so a receipt can say whether the deck ran out of time or
 * ran out of slides.
 */
export async function mapWithDeckBudget(
  items,
  concurrency,
  deadline,
  callback,
  onExpired,
  options = {},
) {
  const now = options.now ?? Date.now;
  const safetyFactor =
    typeof options.safetyFactor === "number" && options.safetyFactor >= 1
      ? options.safetyFactor
      : DEFAULT_SAFETY_FACTOR;
  const minimumSamples = Number.isSafeInteger(options.minimumSamples)
    ? Math.max(1, options.minimumSamples)
    : MINIMUM_SAMPLES;

  const output = new Array(items.length);
  const durations = [];
  const telemetry = {
    attempted: 0,
    completed: 0,
    skippedPastDeadline: 0,
    skippedByEstimate: 0,
    estimateMillis: null,
    observedMedianMillis: null,
    observedMaxMillis: null,
    remainingAtFirstSkipMillis: null,
  };
  let next = 0;

  const noteSkip = (key) => {
    telemetry[key] += 1;
    if (telemetry.remainingAtFirstSkipMillis === null) {
      telemetry.remainingAtFirstSkipMillis = Math.max(0, deadline - now());
    }
  };

  async function pump() {
    while (next < items.length) {
      const index = next++;
      const startedAt = now();
      const remaining = deadline - startedAt;
      if (remaining <= 0) {
        noteSkip("skippedPastDeadline");
        output[index] = onExpired(items[index]);
        continue;
      }
      // An estimate is only allowed to veto a slide once it rests on enough
      // samples; before that the budget is spent optimistically, exactly as it
      // was before, so a single slow first slide cannot gate the whole deck.
      const estimate =
        durations.length >= minimumSamples
          ? Math.round(medianMillis(durations) * safetyFactor)
          : null;
      telemetry.estimateMillis = estimate;
      if (estimate !== null && estimate > remaining) {
        noteSkip("skippedByEstimate");
        output[index] = onExpired(items[index]);
        continue;
      }
      telemetry.attempted += 1;
      output[index] = await callback(items[index]);
      telemetry.completed += 1;
      durations.push(Math.max(0, now() - startedAt));
      telemetry.observedMedianMillis = medianMillis(durations);
      telemetry.observedMaxMillis = Math.max(...durations);
    }
  }

  await Promise.all(
    Array.from({ length: Math.max(1, Math.min(concurrency, items.length)) }, pump),
  );
  return { output, telemetry };
}
