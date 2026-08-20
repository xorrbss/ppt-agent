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

test("compile-time registry resolves the two supported server-issued profiles", () => {
  assert.deepEqual(Object.keys(PRESENTATION_DRIVER_REGISTRY), [
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
      "standard",
    );
  }
});

test("unknown and future server identities fail closed with stable codes", () => {
  assert.throws(
    () =>
      resolveServerIssuedPresentationProfile({
        ...serverResponse("authored"),
        profile: {
          ...serverResponse("authored").profile,
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
        ...serverResponse("authored"),
        profile: {
          ...serverResponse("authored").profile,
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
      ...serverResponse("authored"),
      capabilities: serverResponse("template-v2").capabilities,
    }),
    null,
  );
});

test("profile/capability conflicts fail before an adapter is selected", () => {
  const authored = serverResponse("authored");
  assert.throws(
    () =>
      resolveServerIssuedPresentationProfile({
        profile: authored.profile,
        capabilities: serverResponse("template-v2").capabilities,
      }),
    (error: unknown) =>
      error instanceof PresentationProfileResolutionError &&
      error.code === "presentation_capability_driver_conflict",
  );
  assert.throws(
    () =>
      resolveServerIssuedPresentationProfile({
        profile: {
          ...authored.profile,
          content_model: "template-v2",
        },
        capabilities: authored.capabilities,
      }),
    (error: unknown) =>
      error instanceof PresentationProfileResolutionError &&
      error.code === "presentation_profile_driver_conflict",
  );
});

test("root client hints cannot spoof a server-issued profile", () => {
  const response = {
    ...serverResponse("template-v2"),
    driver_id: "authored",
    generation_mode: "standard",
    content_model: "authored",
  };

  const resolved = resolveServerIssuedPresentationProfile(response);

  assert.equal(resolved.profile.driver_id, "template-v2");
  assert.equal(resolved.driver.export_adapter_id, "template-v2-general");
});

test("selectors consume immutable capabilities and authored alternatives", () => {
  const templateV2 = resolveServerIssuedPresentationProfile(
    serverResponse("template-v2"),
  );
  const authored = resolveServerIssuedPresentationProfile(
    serverResponse("authored"),
  );

  assert.equal(
    presentationCapabilitySelectors.canEditElements(templateV2.capabilities),
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
