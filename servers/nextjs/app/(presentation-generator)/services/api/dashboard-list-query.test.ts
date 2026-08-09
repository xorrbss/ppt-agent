import assert from "node:assert/strict";
import test from "node:test";

import {
  buildPresentationListPath,
  normalizePresentationListEnvelope,
} from "./dashboard-list-query.ts";

test("presentation list query trims and encodes server-side search options", () => {
  assert.equal(
    buildPresentationListPath({
      q: "  분기 계획 & 100%  ",
      page: 3,
      pageSize: 24,
      sort: "asc",
    }),
    "/api/v1/ppt/presentation/all?q=%EB%B6%84%EA%B8%B0+%EA%B3%84%ED%9A%8D+%26+100%25&page=3&page_size=24&sort=asc",
  );
});

test("presentation list response keeps legacy arrays compatible", () => {
  assert.deepEqual(
    normalizePresentationListEnvelope([{ id: "legacy" }], {
      page: 2,
      pageSize: 10,
    }),
    {
      items: [{ id: "legacy" }],
      total: 1,
      page: 2,
      page_size: 10,
    },
  );
});
