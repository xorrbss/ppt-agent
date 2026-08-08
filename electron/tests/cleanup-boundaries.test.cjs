const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const {
  exceedsCleanupLimit,
  isContainedCleanupPath,
  isDirectCleanupChild,
  isProtectedTemporaryPath,
  MAX_CACHE_DIRECTORIES_PER_RECOVERY,
} = require("../app_dist/utils/cleanup-boundaries.js");

test("cache cleanup limit is shared across all recovery roots", () => {
  assert.equal(exceedsCleanupLimit(0, MAX_CACHE_DIRECTORIES_PER_RECOVERY), false);
  assert.equal(exceedsCleanupLimit(5, 3), false);
  assert.equal(exceedsCleanupLimit(5, 4), true);
  assert.equal(exceedsCleanupLimit(-1, 1), true);
});

test("cache cleanup stays within its app-owned root", () => {
  const cacheRoot = path.resolve("C:\\presenton-test-cache");
  assert.equal(isContainedCleanupPath(cacheRoot, path.join(cacheRoot, "chrome", "123456")), true);
  assert.equal(isContainedCleanupPath(cacheRoot, cacheRoot), false);
  assert.equal(isContainedCleanupPath(cacheRoot, path.join(cacheRoot, "..", "outside")), false);
});

test("stale Chromium recovery accepts only direct non-temporary cache children", () => {
  const cacheRoot = path.resolve("C:\\presenton-test-cache");
  assert.equal(isDirectCleanupChild(cacheRoot, path.join(cacheRoot, "old_Cache_001")), true);
  assert.equal(
    isDirectCleanupChild(cacheRoot, path.join(cacheRoot, "old_Cache_001", "nested")),
    false,
  );
  assert.equal(isDirectCleanupChild(cacheRoot, path.join(cacheRoot, ".tmp-user-cache")), false);
  assert.equal(isProtectedTemporaryPath(path.join(cacheRoot, ".tmp-user-cache", "data")), true);
  assert.equal(MAX_CACHE_DIRECTORIES_PER_RECOVERY, 8);
});
