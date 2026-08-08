const assert = require("node:assert/strict");
const test = require("node:test");

const {
  classifyNavigationUrl,
  classifyWindowOpenUrl,
  PopupRequestGuard,
} = require("../app_dist/utils/navigation-policy.js");

const ORIGIN = "http://127.0.0.1:43127";

test("only an exact same-origin presentation target becomes an internal popup", () => {
  const disposition = classifyWindowOpenUrl(
    `${ORIGIN}/presentation?id=target-1&workspace_staging=template`,
    ORIGIN,
  );
  assert.deepEqual(disposition, {
    action: "internal-presentation",
    key: `${ORIGIN}/presentation?id=target-1`,
    url: `${ORIGIN}/presentation?id=target-1&workspace_staging=template`,
  });

  for (const candidate of [
    "https://127.0.0.1:43127/presentation?id=target-1",
    "http://127.0.0.1:43128/presentation?id=target-1",
    "http://127.0.0.1.evil.example:43127/presentation?id=target-1",
    "http://user@127.0.0.1:43127/presentation?id=target-1",
    `${ORIGIN}/presentation-evil?id=target-1`,
    `${ORIGIN}/presentation`,
  ]) {
    assert.notEqual(
      classifyWindowOpenUrl(candidate, ORIGIN).action,
      "internal-presentation",
      candidate,
    );
  }
});

test("presentation children stay confined while ordinary web URLs remain external", () => {
  assert.equal(
    classifyNavigationUrl(
      `${ORIGIN}/presentation?id=target-1`,
      ORIGIN,
      "presentation",
    ),
    "internal",
  );
  assert.equal(
    classifyNavigationUrl(`${ORIGIN}/dashboard`, ORIGIN, "presentation"),
    "deny",
  );
  assert.deepEqual(
    classifyWindowOpenUrl("https://example.com/help", ORIGIN),
    { action: "external" },
  );
  for (const candidate of [
    "javascript:alert(1)",
    "data:text/html,payload",
    "file:///tmp/payload",
  ]) {
    assert.deepEqual(classifyWindowOpenUrl(candidate, ORIGIN), {
      action: "deny",
    });
  }
});

test("the only non-web exception is the inert staging placeholder", () => {
  assert.deepEqual(classifyWindowOpenUrl("about:blank", ORIGIN), {
    action: "internal-presentation-staging",
  });
  for (const candidate of [
    "about:srcdoc",
    "about:blank#payload",
    "about:blank?payload=1",
  ]) {
    assert.deepEqual(classifyWindowOpenUrl(candidate, ORIGIN), {
      action: "deny",
    });
  }
  assert.deepEqual(classifyWindowOpenUrl("about:blank", undefined), {
    action: "deny",
  });
});

test("popup reservations are idempotent but recover after failure or timeout", () => {
  let now = 100;
  const guard = new PopupRequestGuard(5_000, () => now);
  assert.equal(guard.reserve("target-1"), true);
  assert.equal(guard.reserve("target-1"), false);
  guard.release("target-1");
  assert.equal(guard.reserve("target-1"), true);
  now = 5_101;
  assert.equal(guard.reserve("target-1"), true);
});
