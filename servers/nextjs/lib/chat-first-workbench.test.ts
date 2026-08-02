import assert from "node:assert/strict";
import test from "node:test";

import {
  isChatFirstWorkbenchEnabled,
  resolveChatFirstWorkbenchRollout,
  resolvePresentationCreateThemeStrategy,
} from "./chat-first-workbench.ts";

test("chat-first feature flag is strictly opt-in", () => {
  assert.equal(isChatFirstWorkbenchEnabled(undefined), false);
  assert.equal(isChatFirstWorkbenchEnabled("false"), false);
  assert.equal(isChatFirstWorkbenchEnabled("unexpected"), false);
  assert.equal(isChatFirstWorkbenchEnabled("true"), true);
  assert.equal(isChatFirstWorkbenchEnabled(" 1 "), true);
  assert.equal(isChatFirstWorkbenchEnabled("ON"), true);
});

test("chat-first rollout permits legacy only for an explicit build or runtime rollback", () => {
  const ready = {
    supported: true,
    timeline: true,
    commands: true,
    events: true,
  };
  assert.deepEqual(resolveChatFirstWorkbenchRollout(false), {
    mode: "legacy",
    reason: "build_flag_disabled",
  });
  assert.deepEqual(resolveChatFirstWorkbenchRollout(true), { mode: "checking" });
  assert.deepEqual(resolveChatFirstWorkbenchRollout(true, ready), { mode: "chat" });
  assert.deepEqual(
    resolveChatFirstWorkbenchRollout(true, {
      ...ready,
      commands: false,
      reason: "chat_first_feature_disabled",
    }),
    { mode: "legacy", reason: "chat_first_feature_disabled" },
  );
});

test("chat-first rollout fails closed for unavailable and misconfigured backends", () => {
  for (const capability of [
    {
      supported: false,
      timeline: false,
      commands: false,
      events: false,
      reason: "unavailable",
    },
    {
      supported: false,
      timeline: false,
      commands: false,
      events: false,
      reason: "chat_first_runtime_profile_required",
    },
    {
      supported: true,
      timeline: true,
      commands: false,
      events: true,
      reason: "asset_scanner_unavailable",
    },
  ]) {
    assert.deepEqual(resolveChatFirstWorkbenchRollout(true, capability), {
      mode: "blocked",
      reason: capability.reason,
    });
  }
});

test("explicit runtime rollback restores the complete legacy upload theme contract", () => {
  const rollback = resolveChatFirstWorkbenchRollout(true, {
    supported: true,
    timeline: true,
    commands: false,
    events: true,
    reason: "chat_first_feature_disabled",
  });
  assert.equal(rollback.mode, "legacy");
  assert.equal(resolvePresentationCreateThemeStrategy(rollback.mode), "legacy-patch");
  assert.equal(resolvePresentationCreateThemeStrategy("chat"), "embedded");
  assert.equal(resolvePresentationCreateThemeStrategy("checking"), "blocked");
  assert.equal(resolvePresentationCreateThemeStrategy("blocked"), "blocked");
});
