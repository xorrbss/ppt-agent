import assert from "node:assert/strict";
import test from "node:test";

import {
  GENERATION_MODE_COMPATIBILITY,
  PRESENTATION_DRIVER_REGISTRY,
  PresentationProfileResolutionError,
  encodeLegacyGenerationMode,
  presentationCapabilitySelectors,
  resolveServerIssuedPresentationProfile,
  tryResolveServerIssuedPresentationProfile,
  type PresentationCapabilities,
  type PresentationDriverId,
  type PresentationProfile,
} from "./presentation-profile.ts";

const expected = {
  standard: {
    content_model: "legacy-elements",
    generation_strategy: "standard",
    style_source: "builtin",
    generation_mode: "standard",
  },
  smart: {
    content_model: "smart-html",
    generation_strategy: "smart",
    style_source: "community",
    generation_mode: "smart",
  },
  "template-v2": {
    content_model: "template-v2",
    generation_strategy: "template",
    style_source: "imported",
    generation_mode: "standard",
  },
  authored: {
    content_model: "authored",
    generation_strategy: "authored",
    style_source: "authored",
    generation_mode: "standard",
  },
} as const;

function serverResponse(driverId: PresentationDriverId) {
  const driver = PRESENTATION_DRIVER_REGISTRY[driverId];
  const profile: PresentationProfile = {
    schema_version: 1,
    driver_id: driverId,
    ...expected[driverId],
  };
  const capabilities: PresentationCapabilities = {
    schema_version: 1,
    driver_id: driverId,
    renderer_adapter_id: driver.renderer_adapter_id,
    editor_adapter_id: driver.editor_adapter_id,
    editor: { ...driver.editor },
    agent_tool_capability: driver.agent_tool_capability,
    export_adapter_id: driver.export_adapter_id,
    export_editable_pptx: driver.export_editable_pptx,
  };
  return { profile, capabilities };
}

test("compile-time registry resolves all four server-issued profiles", () => {
  assert.deepEqual(Object.keys(PRESENTATION_DRIVER_REGISTRY), [
    "standard",
    "smart",
    "template-v2",
    "authored",
  ]);

  for (const driverId of Object.keys(
    PRESENTATION_DRIVER_REGISTRY,
  ) as PresentationDriverId[]) {
    const resolved = resolveServerIssuedPresentationProfile(
      serverResponse(driverId),
    );
    assert.equal(resolved.profile.driver_id, driverId);
    assert.equal(resolved.driver, PRESENTATION_DRIVER_REGISTRY[driverId]);
    assert.equal(
      encodeLegacyGenerationMode(resolved.profile),
      driverId === "smart" ? "smart" : "standard",
    );
  }
});

test("unknown and future server identities fail closed with stable codes", () => {
  assert.throws(
    () =>
      resolveServerIssuedPresentationProfile({
        ...serverResponse("standard"),
        profile: {
          ...serverResponse("standard").profile,
          driver_id: "future",
        },
      }),
    (error: unknown) =>
      error instanceof PresentationProfileResolutionError &&
      error.code === "unsupported_presentation_driver",
  );
  assert.throws(
    () =>
      resolveServerIssuedPresentationProfile({
        ...serverResponse("standard"),
        profile: {
          ...serverResponse("standard").profile,
          schema_version: 2,
        },
      }),
    (error: unknown) =>
      error instanceof PresentationProfileResolutionError &&
      error.code === "unsupported_presentation_profile_version",
  );
});

test("UI-safe resolution grants no capability to malformed profiles", () => {
  assert.equal(tryResolveServerIssuedPresentationProfile({}), null);
  assert.equal(
    tryResolveServerIssuedPresentationProfile({
      ...serverResponse("standard"),
      capabilities: serverResponse("smart").capabilities,
    }),
    null,
  );
});

test("profile/capability conflicts fail before an adapter is selected", () => {
  const standard = serverResponse("standard");
  assert.throws(
    () =>
      resolveServerIssuedPresentationProfile({
        profile: standard.profile,
        capabilities: serverResponse("authored").capabilities,
      }),
    (error: unknown) =>
      error instanceof PresentationProfileResolutionError &&
      error.code === "presentation_capability_driver_conflict",
  );
  assert.throws(
    () =>
      resolveServerIssuedPresentationProfile({
        profile: {
          ...standard.profile,
          content_model: "smart-html",
        },
        capabilities: standard.capabilities,
      }),
    (error: unknown) =>
      error instanceof PresentationProfileResolutionError &&
      error.code === "presentation_profile_driver_conflict",
  );
});

test("root client hints cannot spoof a server-issued profile", () => {
  const response = {
    ...serverResponse("standard"),
    driver_id: "authored",
    generation_mode: "smart",
    content_model: "authored",
    client_capabilities: { smart_selection: true },
  };

  const resolved = resolveServerIssuedPresentationProfile(response);

  assert.equal(resolved.profile.driver_id, "standard");
  assert.equal(resolved.capabilities.editor.smart_selection, false);
  assert.equal(resolved.driver.export_adapter_id, "legacy-general");
});

test("selectors consume immutable capabilities and authored alternatives", () => {
  const standard = resolveServerIssuedPresentationProfile(
    serverResponse("standard"),
  );
  const smart = resolveServerIssuedPresentationProfile(serverResponse("smart"));
  const authored = resolveServerIssuedPresentationProfile(
    serverResponse("authored"),
  );

  assert.equal(
    presentationCapabilitySelectors.canEditElements(standard.capabilities),
    true,
  );
  assert.equal(
    presentationCapabilitySelectors.canUseSmartSelection(smart.capabilities),
    true,
  );
  assert.equal(
    presentationCapabilitySelectors.canEditElements(authored.capabilities),
    false,
  );
  assert.equal(
    presentationCapabilitySelectors.canRegenerateSlideImage(
      authored.capabilities,
    ),
    true,
  );
  assert.equal(Object.isFrozen(authored.capabilities), true);
  assert.equal(Object.isFrozen(authored.capabilities.editor), true);
});

test("generation_mode compatibility has an explicit removal condition", () => {
  assert.equal(
    GENERATION_MODE_COMPATIBILITY.source_of_truth,
    "derived-presentation-profile",
  );
  assert.match(
    GENERATION_MODE_COMPATIBILITY.removal_condition,
    /^Remove only after/,
  );
});
