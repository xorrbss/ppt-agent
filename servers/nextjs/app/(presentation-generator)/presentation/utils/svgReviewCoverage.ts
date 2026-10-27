/** Read the server's coverage projection; never infer coverage from list positions. */
export function svgReviewCoverage(theme: unknown): { unreviewedPages: number[]; flowUnreviewedPages: number[] } {
  const record = (value: unknown): Record<string, unknown> =>
    value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const indices = (value: unknown): number[] => Array.isArray(value)
    ? value.filter((index): index is number => Number.isInteger(index) && index >= 0) : [];
  const svg = record(record(theme).svg);
  const coverage = record(svg.reviewCoverage);
  const review = record(svg.designReview);
  const unreviewedPages = indices(coverage.unreviewedPages ?? review.unreviewedPages);
  return { unreviewedPages,
    flowUnreviewedPages: [...new Set([...indices(review.flowUnreviewedPages), ...indices(coverage.unreviewedPages)])] };
}
