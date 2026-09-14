/**
 * What preserving original content would cost this particular deck.
 *
 * The mode carries text and images over verbatim but cannot rebuild tables or
 * charts, and it keeps anything it fails to recompose as a flat image. Whether
 * that costs the user anything depends entirely on the source: most decks lose
 * nothing and should redesign in a single click, so this is what decides
 * between running straight through and stopping to ask.
 *
 * Kept free of runtime imports (the summary arrives as a type) so it can be
 * exercised on its own.
 */

import type { SourceContentAnalysisSummary } from "../../../services/api/source-content/contract";

/** The losses, phrased for the user. Empty means nothing is left behind. */
export const sourceContentLosses = (
  summary: SourceContentAnalysisSummary,
): string[] => {
  const losses: string[] = [];
  if (summary.charts > 0) losses.push(`차트 ${summary.charts}개가 제외됩니다.`);
  if (summary.rasterFallbackBlocks > 0) {
    losses.push(
      `다시 구성할 수 없는 ${summary.rasterFallbackBlocks}개 블록은 편집할 수 없는 이미지로 보관됩니다.`,
    );
  }
  return losses;
};
