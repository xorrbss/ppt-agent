const assert = require("node:assert/strict");
const test = require("node:test");

const {
  classifyNavigationUrl,
  classifyWindowOpenUrl,
  getTrustedPresentationWorkspace,
  isAnonymousWindowName,
  PopupRequestGuard,
} = require("../app_dist/utils/navigation-policy.js");

const APP_ORIGIN = "http://127.0.0.1:43127";

test("allows only exact same-origin presentation workspaces as internal popups", () => {
  const allowed = classifyWindowOpenUrl(
    `${APP_ORIGIN}/presentation?id=deck-1&workspace_staging=template`,
    APP_ORIGIN,
  );
  assert.deepEqual(allowed, {
    action: "internal-presentation",
    key: `${APP_ORIGIN}/presentation?id=deck-1`,
    url: `${APP_ORIGIN}/presentation?id=deck-1&workspace_staging=template`,
  });

  assert.equal(
    getTrustedPresentationWorkspace(
      `${APP_ORIGIN}/presentation/?id=deck-2`,
      APP_ORIGIN,
    )?.key,
    `${APP_ORIGIN}/presentation?id=deck-2`,
  );
});

test("allows only the inert web-flow placeholder before a trusted presentation", () => {
  assert.deepEqual(classifyWindowOpenUrl("about:blank", APP_ORIGIN), {
    action: "internal-presentation-staging",
  });
  assert.deepEqual(classifyWindowOpenUrl("about:blank", undefined), {
    action: "deny",
  });
  assert.deepEqual(classifyWindowOpenUrl("about:srcdoc", APP_ORIGIN), {
    action: "deny",
  });
});

test("accepts Chromium's unnamed _blank staging frame but rejects reusable names", () => {
  assert.equal(isAnonymousWindowName(""), true);
  assert.equal(isAnonymousWindowName("_blank"), true);
  assert.equal(isAnonymousWindowName("template-target"), false);
  assert.equal(isAnonymousWindowName("_self"), false);
});

test("rejects origin confusion and non-workspace paths from internal windows", () => {
  const blocked = [
    "https://127.0.0.1:43127/presentation?id=deck-1",
    "http://127.0.0.1:43128/presentation?id=deck-1",
    "http://127.0.0.1.evil.example:43127/presentation?id=deck-1",
    "http://user@127.0.0.1:43127/presentation?id=deck-1",
    `${APP_ORIGIN}/presentation-evil?id=deck-1`,
    `${APP_ORIGIN}/presentation%2Fchild?id=deck-1`,
    `${APP_ORIGIN}/presentation`,
    `${APP_ORIGIN}/presentation?id=%20%20`,
  ];

  for (const candidate of blocked) {
    assert.notEqual(
      classifyWindowOpenUrl(candidate, APP_ORIGIN).action,
      "internal-presentation",
      candidate,
    );
  }
});

test("preserves external-open and unsupported-scheme denial boundaries", () => {
  assert.deepEqual(
    classifyWindowOpenUrl("https://example.com/releases", APP_ORIGIN),
    { action: "external" },
  );
  assert.deepEqual(
    classifyWindowOpenUrl(`${APP_ORIGIN}/dashboard`, APP_ORIGIN),
    { action: "external" },
  );
  assert.deepEqual(
    classifyWindowOpenUrl(
      "https://user:secret@example.com/releases",
      APP_ORIGIN,
    ),
    { action: "external" },
  );
  for (const candidate of [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "data:text/html,hello",
    "not a url",
  ]) {
    assert.deepEqual(classifyWindowOpenUrl(candidate, APP_ORIGIN), {
      action: "deny",
    });
  }
});

test("will-navigate keeps app pages internal but confines workspace windows", () => {
  assert.equal(
    classifyNavigationUrl(`${APP_ORIGIN}/dashboard`, APP_ORIGIN, "app"),
    "internal",
  );
  assert.equal(
    classifyNavigationUrl(
      `${APP_ORIGIN}/presentation?id=deck-1`,
      APP_ORIGIN,
      "presentation-staging",
    ),
    "internal",
  );
  assert.equal(
    classifyNavigationUrl(`${APP_ORIGIN}/dashboard`, APP_ORIGIN, "presentation"),
    "deny",
  );
  assert.equal(
    classifyNavigationUrl("https://example.com", APP_ORIGIN, "presentation"),
    "external",
  );
  assert.equal(
    classifyNavigationUrl("file:///tmp/payload", APP_ORIGIN, "app"),
    "deny",
  );
});

test("popup reservations suppress immediate retries but recover after release or timeout", () => {
  let now = 1_000;
  const guard = new PopupRequestGuard(5_000, () => now);
  assert.equal(guard.reserve("deck-1"), true);
  assert.equal(guard.reserve("deck-1"), false);
  guard.release("deck-1");
  assert.equal(guard.reserve("deck-1"), true);
  now = 6_001;
  assert.equal(guard.reserve("deck-1"), true);
});
