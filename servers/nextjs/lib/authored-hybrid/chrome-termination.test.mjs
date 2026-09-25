import assert from "node:assert/strict";
import test from "node:test";

import { __testOnlyTerminateChrome } from "./chrome-runner.ts";

test("completed Windows capture waits for graceful exit before fallback helpers", async () => {
  const calls = [];
  const child = {
    pid: 123,
    exitCode: null,
    signalCode: null,
    kill() {
      calls.push("kill");
    },
  };

  await __testOnlyTerminateChrome(child, "C:\\capture-profile", true, {
    platform: "win32",
    async waitForExit(target) {
      calls.push("wait");
      target.exitCode = 0;
    },
    async taskkill() {
      calls.push("taskkill");
    },
    async terminateProfile() {
      calls.push("profile");
    },
  });

  assert.deepEqual(calls, ["wait", "wait", "profile"]);
});
