import assert from "node:assert/strict";
import test from "node:test";

import {
  canReadChatFirstWorkspace,
  isChatFirstWorkbenchEnabled,
  resolveChatFirstWorkbenchRollout,
  resolvePresentationCreateThemeStrategy,
  shouldApplyChatFirstCapabilityBroadcast,
  shouldCommitChatFirstCapabilityRequest,
} from "./chat-first-workbench.ts";

test("read capability remains independent from command rollout", () => {
  assert.equal(
    canReadChatFirstWorkspace({
      supported: true,
      timeline: true,
      commands: false,
      events: true,
      reason: "chat_first_kill_switch_enabled",
    }),
    true,
  );
  assert.equal(
    canReadChatFirstWorkspace({
      supported: true,
      timeline: false,
      commands: false,
      events: true,
    }),
    false,
  );
});

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
  assert.deepEqual(resolveChatFirstWorkbenchRollout(true), {
    mode: "checking",
  });
  assert.deepEqual(resolveChatFirstWorkbenchRollout(true, ready), {
    mode: "chat",
  });
  assert.deepEqual(
    resolveChatFirstWorkbenchRollout(true, {
      ...ready,
      commands: false,
      reason: "chat_first_feature_disabled",
    }),
    { mode: "legacy", reason: "chat_first_feature_disabled" },
  );
  for (const reason of [
    "chat_first_kill_switch_enabled",
    "chat_first_cohort_excluded",
  ]) {
    assert.deepEqual(
      resolveChatFirstWorkbenchRollout(true, {
        ...ready,
        commands: false,
        reason,
      }),
      { mode: "legacy", reason },
    );
  }
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
  ]) {
    assert.deepEqual(resolveChatFirstWorkbenchRollout(true, capability), {
      mode: "blocked",
      reason: capability.reason,
    });
  }
});

test("timeline remains reachable when command writes are disabled", () => {
  assert.deepEqual(
    resolveChatFirstWorkbenchRollout(true, {
      supported: true,
      timeline: true,
      commands: false,
      events: true,
      reason: "asset_scanner_unavailable",
    }),
    { mode: "chat" },
  );
});

test("a kill-switch broadcast changes both tabs and rejects stale or cross-deck state", () => {
  const rollback = {
    kind: "capability",
    key: "deck-1",
    snapshot: {
      checkedAt: 20,
      capability: {
        supported: true,
        timeline: true,
        commands: false,
        events: true,
        reason: "chat_first_kill_switch_enabled",
      },
    },
  };

  for (const tabCheckedAt of [10, 19]) {
    assert.equal(
      shouldApplyChatFirstCapabilityBroadcast(rollback, "deck-1", tabCheckedAt),
      true,
    );
    assert.deepEqual(
      resolveChatFirstWorkbenchRollout(true, rollback.snapshot.capability),
      { mode: "legacy", reason: "chat_first_kill_switch_enabled" },
    );
  }
  assert.equal(
    shouldApplyChatFirstCapabilityBroadcast(rollback, "deck-1", 21),
    false,
  );
  assert.equal(
    shouldApplyChatFirstCapabilityBroadcast(rollback, "deck-2", 10),
    false,
  );
});

test("a delayed capability response cannot overwrite a newer kill-switch generation", async () => {
  let generation = 4;
  let mode = "checking";
  let releaseResponse!: () => void;
  const delayedResponse = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  const requestGeneration = generation;
  const request = delayedResponse.then(() => {
    if (shouldCommitChatFirstCapabilityRequest(requestGeneration, generation)) {
      mode = "chat";
    }
  });

  generation += 1;
  mode = "legacy";
  releaseResponse();
  await request;

  assert.equal(mode, "legacy");
  assert.equal(
    shouldCommitChatFirstCapabilityRequest(
      requestGeneration,
      requestGeneration,
    ),
    true,
  );
});

test("runtime rollback remains safety-dominant across inverse request and tab races", () => {
  const rollback = {
    supported: true,
    timeline: true,
    commands: false,
    events: true,
    reason: "chat_first_kill_switch_enabled",
  };
  const enabled = {
    supported: true,
    timeline: true,
    commands: true,
    events: true,
  };

  // A rollback response may overtake a newer ordinary capability request.
  assert.equal(shouldCommitChatFirstCapabilityRequest(4, 5, rollback), true);
  assert.equal(shouldCommitChatFirstCapabilityRequest(4, 5, enabled), false);

  // A timestamp-newer broadcast cannot lift a rollback already observed in
  // this tab; only this tab's own fresh server response may do that.
  assert.equal(
    shouldApplyChatFirstCapabilityBroadcast(
      {
        kind: "capability",
        key: "deck-1",
        snapshot: { checkedAt: 30, capability: enabled },
      },
      "deck-1",
      20,
      rollback,
    ),
    false,
  );
  assert.equal(
    shouldApplyChatFirstCapabilityBroadcast(
      {
        kind: "capability",
        key: "deck-1",
        snapshot: { checkedAt: 30, capability: rollback },
      },
      "deck-1",
      20,
      enabled,
    ),
    true,
  );
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
  assert.equal(
    resolvePresentationCreateThemeStrategy(rollback.mode),
    "legacy-patch",
  );
  assert.equal(resolvePresentationCreateThemeStrategy("chat"), "embedded");
  assert.equal(resolvePresentationCreateThemeStrategy("checking"), "blocked");
  assert.equal(resolvePresentationCreateThemeStrategy("blocked"), "blocked");
});
