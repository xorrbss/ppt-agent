export type DraftGridPage = {
  index: number;
  author: string;
  render: string;
  quality: string;
  pageVersion: string;
};

export type DraftTileState = "rendered" | "authored" | "pending";

export type DraftTile = {
  index: number;
  state: DraftTileState;
  pageVersion: string;
  quality: string;
};

/** A tile may only request an image once the render is stored and a version exists to key it by. */
function tileState(page: DraftGridPage | undefined): DraftTileState {
  if (!page) return "pending";
  if (page.render === "completed" && page.pageVersion) return "rendered";
  if (page.author === "completed") return "authored";
  return "pending";
}

/**
 * One tile per page of the deck. Pages that have no checkpoint row yet are absent from the
 * response, so the tile count comes from totalPages (widened if a row exceeds it).
 */
export function deriveDraftTiles(pages: readonly DraftGridPage[], totalPages: number): DraftTile[] {
  const byIndex = new Map<number, DraftGridPage>();
  let highest = -1;
  for (const page of pages) {
    if (!Number.isInteger(page.index) || page.index < 0) continue;
    byIndex.set(page.index, page);
    highest = Math.max(highest, page.index);
  }
  const size = Math.max(Number.isFinite(totalPages) ? Math.floor(totalPages) : 0, highest + 1, 0);
  const tiles: DraftTile[] = [];
  for (let index = 0; index < size; index += 1) {
    const page = byIndex.get(index);
    tiles.push({
      index,
      state: tileState(page),
      pageVersion: page?.pageVersion ?? "",
      quality: page?.quality ?? "unconfirmed",
    });
  }
  return tiles;
}

export function countRenderedTiles(tiles: readonly DraftTile[]): number {
  return tiles.reduce((total, tile) => total + (tile.state === "rendered" ? 1 : 0), 0);
}

/** Integer percentage clamped to 0..100; an unknown total is 0 rather than NaN. */
export function renderedPercent(rendered: number, total: number): number {
  if (!(total > 0)) return 0;
  return Math.min(100, Math.max(0, Math.round((rendered / total) * 100)));
}

/**
 * The URL embeds job id and page version, so an unchanged page keeps an identical src and the
 * browser does not refetch it on a re-poll; a re-rendered page gets a new URL.
 */
export function draftImageUrl(presentationId: string, jobId: string, pageIndex: number, pageVersion: string): string {
  return `/api/v1/ppt/presentation/${encodeURIComponent(presentationId)}/draft-preview/${pageIndex}/image`
    + `?job_id=${encodeURIComponent(jobId)}&page_version=${encodeURIComponent(pageVersion)}`;
}

/** Highest-numbered rendered tile, or null when nothing is rendered yet. */
export function newestRenderedIndex(tiles: readonly DraftTile[]): number | null {
  for (let i = tiles.length - 1; i >= 0; i -= 1) {
    if (tiles[i].state === "rendered") return tiles[i].index;
  }
  return null;
}
