import assert from "node:assert/strict";
import test from "node:test";

import { getAuthenticatedLanding } from "./authenticatedLanding.ts";

test("authenticated local first run remains in onboarding with an empty config", () => {
  assert.equal(
    getAuthenticatedLanding({
      canChangeKeys: true,
      hasValidConfig: false,
    }),
    "onboarding"
  );
});

test("authenticated users with a valid config continue to upload", () => {
  assert.equal(
    getAuthenticatedLanding({
      canChangeKeys: true,
      hasValidConfig: true,
    }),
    "upload"
  );
});

test("a model availability failure forces onboarding even for structurally valid config", () => {
  assert.equal(
    getAuthenticatedLanding({
      canChangeKeys: true,
      hasValidConfig: true,
      forceOnboarding: true,
    }),
    "onboarding"
  );
});

test("server-managed configuration continues directly to upload", () => {
  assert.equal(
    getAuthenticatedLanding({
      canChangeKeys: false,
      hasValidConfig: false,
      forceOnboarding: true,
    }),
    "upload"
  );
});
