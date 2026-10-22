import assert from "node:assert/strict";
import test from "node:test";
import {
  countRenderedTiles, deriveDraftTiles, draftImageUrl, newestRenderedIndex, renderedPercent,
  type DraftGridPage,
} from "./draftGridModel";

const page = (index: number, author: string, render: string, pageVersion = "v1"): DraftGridPage =>
  ({ index, author, render, quality: "unconfirmed", pageVersion });

test("tiles cover totalPages and mark absent pages pending", () => {
  const tiles = deriveDraftTiles([page(0, "completed", "completed"), page(2, "completed", "pending")], 4);
  assert.deepEqual(tiles.map((tile) => tile.state), ["rendered", "pending", "authored", "pending"]);
});

test("a rendered page without a version is not requested", () => {
  const tiles = deriveDraftTiles([page(0, "completed", "completed", "")], 1);
  assert.equal(tiles[0].state, "authored");
});

test("rows beyond totalPages widen the grid; bad indexes are ignored", () => {
  const tiles = deriveDraftTiles([page(5, "pending", "pending"), page(-1, "completed", "completed")], 2);
  assert.equal(tiles.length, 6);
  assert.equal(tiles[5].state, "pending");
});

test("unknown total with no pages yields no tiles", () => {
  assert.equal(deriveDraftTiles([], Number.NaN).length, 0);
});

test("counts, percent and newest rendered index", () => {
  const tiles = deriveDraftTiles([page(0, "completed", "completed"), page(1, "completed", "completed"), page(3, "completed", "pending")], 4);
  assert.equal(countRenderedTiles(tiles), 2);
  assert.equal(newestRenderedIndex(tiles), 1);
  assert.equal(renderedPercent(2, 4), 50);
  assert.equal(renderedPercent(1, 0), 0);
  assert.equal(renderedPercent(9, 4), 100);
  assert.equal(newestRenderedIndex(deriveDraftTiles([], 3)), null);
});

test("image url is keyed by job and page version", () => {
  const a = draftImageUrl("p/1", "job", 3, "2026-10-22T13:43:34+00:00");
  assert.equal(a, "/api/v1/ppt/presentation/p%2F1/draft-preview/3/image?job_id=job&page_version=2026-10-22T13%3A43%3A34%2B00%3A00");
  assert.notEqual(a, draftImageUrl("p/1", "job", 3, "other"));
});
