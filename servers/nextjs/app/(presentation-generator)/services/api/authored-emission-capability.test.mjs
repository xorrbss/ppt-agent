import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { transform } from "esbuild";

const directory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(directory, "authored-emission-capability.ts");
const statusSourcePath = path.resolve(directory, "../../presentation/components/header/AuthoredEmissionCapabilityStatus.tsx");

function dataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}

async function loadCapabilityClient() {
  const source = await readFile(sourcePath, "utf8");
  const headers = dataModule("export const getHeader = () => ({});\n");
  const api = dataModule("export const getApiUrl = (value) => value;\n");
  const compiled = await transform(
    source
      .replace('from "./header";', `from "${headers}";`)
      .replace('from "@/utils/api";', `from "${api}";`),
    { format: "esm", loader: "ts", target: "esnext" },
  );
  return import(dataModule(compiled.code));
}

async function loadCapabilityStatus() {
  const source = await readFile(statusSourcePath, "utf8");
  const compiled = await transform(source, { format: "esm", loader: "tsx", target: "esnext" });
  return import(dataModule(compiled.code));
}

const digest = (character) => `sha256:${character.repeat(64)}`;

const capabilityMatrix = () => [
  "shape.gradient",
  "shape.shadow",
  "layout.transform",
  "layout.flex",
  "layout.grid",
  "shape.borderRadius",
  "shape.opacity",
  "shape.clip",
].map((capabilityId) => ({
  capabilityId,
  state: "ready",
  decision: "native",
  reasonCode: "serializer_verified",
  receiptDigest: digest("e"),
}));

const receipts = () => ["cjk", "chart", "zBand", "degradation"].map((receiptId) => ({
  receiptId,
  state: "ready",
  decision: "native",
  reasonCode: "receipt_verified",
  receiptDigest: digest("f"),
}));

const releaseCapabilityIds = [
  "shape.gradient", "shape.shadow", "layout.transform", "layout.flex", "layout.grid",
  "shape.borderRadius", "shape.opacity", "shape.clip", "receipt.cjk", "receipt.chart",
  "receipt.zBand", "receipt.degradation", "renderer.attestation", "evidence.visual",
];
const promotionCapabilityIds = [
  "receipt.cjk", "receipt.chart", "receipt.zBand", "receipt.degradation",
  "renderer.attestation", "evidence.visual",
];

function readinessV2(overrides = {}) {
  return {
    schemaVersion: "workbench-capability-projection/v2",
    presentationId: "deck-1",
    presentationRevision: 7,
    canonicalMode: "bundleGraph",
    canonicalDigest: digest("9"),
    manifestDigest: digest("8"),
    degradedFidelityOnly: false,
    releaseProfile: {
      schemaVersion: "authored-release-profile/v2",
      profileId: "profile:staging",
      platformScope: { platform: "windows", renderer: "external-worker", archetypes: ["title"] },
      evaluatedAt: "2026-08-23T00:00:00Z",
      expiresAt: "2026-08-24T00:00:00Z",
      promotionReady: false,
      boundedNativeParityReady: false,
      independentGenerationReady: false,
      promotionCapabilityIds,
      requiredCapabilityIds: promotionCapabilityIds,
      boundedNativeCapabilityIds: releaseCapabilityIds,
      readyReceiptDigests: [],
      profileDefinitionDigest: digest("6"),
      promotionReceiptDigests: [],
      boundedNativeReceiptDigests: [],
      reasonCode: "release_evidence_incomplete",
    },
    capabilities: releaseCapabilityIds.map((capabilityId) => ({
      capabilityId,
      state: capabilityId === "receipt.cjk" ? "ready" : "shadow",
      reasonCode: capabilityId === "receipt.cjk" ? "cjk_receipt_verified" : "serializer_scope_unverified",
      receiptDigest: capabilityId === "receipt.cjk" ? digest("b") : null,
      scope: { platform: "windows", renderer: "external-worker", archetypes: ["title"] },
      serializerId: "presenton-authored-hybrid",
      serializerVersion: "v2",
      expiresAt: capabilityId === "receipt.cjk" ? "2026-08-24T00:00:00Z" : null,
    })),
    rendererAttestation: {
      state: "unattested",
      reasonCode: "renderer_attestation_missing",
      attestationDigest: null,
      environment: { os: "windows", boundary: "external-worker" },
      expiresAt: null,
    },
    killSwitches: [],
    releaseAuthorityReceiptDigests: [],
    ...overrides,
  };
}

function readinessV3(overrides = {}) {
  const v2 = readinessV2();
  const capabilityIds = [
    "ci.requiredChecks", "evidence.visual", "receipt.degradation", "renderer.attestation",
  ];
  const capabilityDigests = Object.fromEntries(capabilityIds.map((capabilityId, index) => [capabilityId, digest(String(index + 1))]));
  return {
    ...v2,
    schemaVersion: "workbench-capability-projection/v3",
    releaseProfile: {
      schemaVersion: "authored-bounded-hybrid-release-readiness/v3",
      profileId: "profile:bounded-hybrid",
      platformScope: { platform: "windows", renderer: "external-worker", archetypes: ["title"] },
      evaluatedAt: "2026-08-23T00:00:00Z",
      expiresAt: "2026-08-24T00:00:00Z",
      deckEvidenceReady: false,
      serializerProfileReady: false,
      nativeSubsetReady: false,
      releaseCommitReady: false,
      promotionReady: false,
      deploymentAuthorized: false,
      fullNativeParityStatus: "notApplicable",
      independentVnextGenerationStatus: "notApplicable",
      deckEvidenceCapabilityIds: ["evidence.visual"],
      serializerProfileCapabilityIds: ["receipt.degradation", "renderer.attestation"],
      nativeSubsetStatus: "notApplicable",
      nativeSubsetCapabilityIds: [],
      unsupportedNativeCapabilityIds: ["receipt.chart", "receipt.cjk", "receipt.imageCrop", "receipt.table", "receipt.zBand"],
      releaseCommitCapabilityIds: ["ci.requiredChecks"],
      allRequiredCapabilityIds: [...capabilityIds].sort(),
      profileDefinitionDigest: digest("6"),
      deckEvidenceReceiptDigests: [],
      serializerProfileReceiptDigests: [],
      nativeSubsetReceiptDigests: [],
      releaseCommitReceiptDigests: [],
      promotionReceiptDigests: [],
      reasonCode: "release_evidence_incomplete",
    },
    capabilities: capabilityIds.map((capabilityId) => ({
      capabilityId,
      state: "ready",
      reasonCode: "receipt_verified",
      receiptDigest: capabilityDigests[capabilityId],
      scope: { platform: "windows", renderer: "external-worker", archetypes: ["title"] },
      serializerId: "presenton-authored-hybrid",
      serializerVersion: "v3",
      expiresAt: "2026-08-24T00:00:00Z",
    })),
    ...overrides,
  };
}

function projection(overrides = {}) {
  return {
    schemaVersion: "workbench-capability-projection/v1",
    presentationId: "deck-1",
    presentationRevision: 7,
    canonicalMode: "bundleGraph",
    canonicalDigest: digest("a"),
    manifestDigest: digest("c"),
    degradedFidelityOnly: false,
    fidelity: { state: "ready", receiptDigest: digest("b") },
    hybrid: {
      text: { state: "ready", receiptDigest: digest("b") },
      image: { state: "shadow" },
      shape: { state: "blocked", reason: "serializer_unavailable" },
    },
    capabilityMatrix: capabilityMatrix(),
    receipts: receipts(),
    readiness: {
      authoredCreationReady: true,
      fidelityExportReady: true,
      fullNativeParityReady: false,
      independentVnextGenerationReady: false,
      promotionReady: false,
    },
    killSwitches: [],
    elements: [],
    projectionDigest: digest("d"),
    ...overrides,
  };
}

test("capability projection retains the immutable manifest binding", async () => {
  const { normalizeAuthoredEmissionCapabilityProjection } = await loadCapabilityClient();
  assert.deepEqual(normalizeAuthoredEmissionCapabilityProjection({ data: projection() }), {
    ...projection(),
    canonicalDigest: digest("a"),
    fidelity: { state: "ready", receiptDigest: digest("b"), reason: undefined },
    hybrid: {
      text: { state: "ready", receiptDigest: digest("b"), reason: undefined },
      image: { state: "shadow", receiptDigest: undefined, reason: undefined },
      shape: { state: "blocked", receiptDigest: undefined, reason: "serializer_unavailable" },
    },
  });
});

test("capability projection rejects a client-side readiness promotion", async () => {
  const { normalizeAuthoredEmissionCapabilityProjection } = await loadCapabilityClient();
  assert.throws(
    () =>
      normalizeAuthoredEmissionCapabilityProjection(
        projection({
          readiness: {
            ...projection().readiness,
            promotionReady: true,
          },
        }),
      ),
    /unsupported readiness promotion/,
  );
});

test("capability projection fails closed without complete server-issued matrix and receipts", async () => {
  const { normalizeAuthoredEmissionCapabilityProjection } = await loadCapabilityClient();
  assert.throws(
    () =>
      normalizeAuthoredEmissionCapabilityProjection(
        projection({
          capabilityMatrix: capabilityMatrix().slice(1),
          receipts: receipts().map((receipt) =>
            receipt.receiptId === "zBand"
              ? { ...receipt, decision: "ownershipFailure", reasonCode: "authored_semantic_ownership_mismatch" }
              : receipt,
          ),
        }),
      ),
    /incomplete capabilityMatrix/,
  );

  assert.throws(
    () =>
      normalizeAuthoredEmissionCapabilityProjection(
        projection({
          capabilityMatrix: capabilityMatrix(),
          receipts: receipts().map((receipt) =>
            receipt.receiptId === "degradation"
              ? { ...receipt, reasonCode: "not a stable reason" }
              : receipt,
          ),
        }),
      ),
    /invalid receipts\[3\]\.reasonCode/,
  );
});

test("v2 release readiness requires every server-issued scoped capability and attestation", async () => {
  const { normalizeAuthoredReleaseReadinessProjectionV2 } = await loadCapabilityClient();
  const normalized = normalizeAuthoredReleaseReadinessProjectionV2({ data: readinessV2() });
  assert.equal(normalized.releaseProfile.promotionReady, false);
  assert.equal(normalized.capabilities.length, 14);
  assert.equal(normalized.rendererAttestation.state, "unattested");
  assert.equal(normalized.exportAuthorityClaim, undefined);
  const profileScoped = readinessV2({
    releaseProfile: {
      ...readinessV2().releaseProfile,
      promotionCapabilityIds: ["receipt.cjk"],
      requiredCapabilityIds: ["receipt.cjk"],
      boundedNativeCapabilityIds: ["shape.gradient", "receipt.cjk"],
    },
    capabilities: readinessV2().capabilities.filter((capability) =>
      ["shape.gradient", "receipt.cjk"].includes(capability.capabilityId),
    ),
  });
  assert.deepEqual(
    normalizeAuthoredReleaseReadinessProjectionV2(profileScoped).releaseProfile.boundedNativeCapabilityIds,
    ["shape.gradient", "receipt.cjk"],
  );
  assert.throws(
    () => normalizeAuthoredReleaseReadinessProjectionV2(readinessV2({ capabilities: readinessV2().capabilities.slice(1) })),
    /incomplete capabilities/,
  );
});

test("v2 validates exact claim pins only when an export authority claim is issued", async () => {
  const { normalizeAuthoredReleaseReadinessProjectionV2 } = await loadCapabilityClient();
  const pinned = readinessV2({
    releaseProfile: {
      ...readinessV2().releaseProfile,
      promotionReceiptDigests: [digest("7")],
    },
    releaseAuthorityReceiptDigests: [digest("7")],
    releaseProjectionDigest: digest("5"),
    exportAuthorityClaim: {
      schemaVersion: "authored-export-authority-claim/v2",
      kind: "promotion",
      profileId: "profile:staging",
      profileDefinitionDigest: digest("6"),
      projectionDigest: digest("5"),
      receiptDigests: [digest("7")],
      state: "ready",
    },
  });
  assert.equal(normalizeAuthoredReleaseReadinessProjectionV2(pinned).exportAuthorityClaim?.state, "ready");
  assert.throws(
    () => normalizeAuthoredReleaseReadinessProjectionV2({ ...pinned, releaseAuthorityReceiptDigests: [digest("4")] }),
    /mismatched exportAuthorityClaim\.receiptDigests/,
  );
});

test("v3 displays only server-issued bounded release readiness and rejects profile drift", async () => {
  const { normalizeAuthoredReleaseReadinessProjectionV3 } = await loadCapabilityClient();
  const serverIssued = readinessV3({
    releaseProfile: {
      ...readinessV3().releaseProfile,
      deckEvidenceReady: true,
      serializerProfileReady: true,
      nativeSubsetReady: false,
      releaseCommitReady: true,
      promotionReady: true,
      deploymentAuthorized: false,
      deckEvidenceReceiptDigests: [digest("2")],
      serializerProfileReceiptDigests: [digest("3"), digest("4")],
      nativeSubsetReceiptDigests: [],
      releaseCommitReceiptDigests: [digest("1")],
      promotionReceiptDigests: [digest("1"), digest("2"), digest("3"), digest("4")],
    },
    rendererAttestation: {
      state: "attested",
      reasonCode: "renderer_attestation_verified",
      attestationDigest: digest("a"),
      environment: { os: "windows", boundary: "external-worker" },
      expiresAt: "2026-08-24T00:00:00Z",
    },
    releaseAuthorityReceiptDigests: [digest("1"), digest("2"), digest("3"), digest("4")],
    releaseProjectionDigest: digest("5"),
    exportAuthorityClaim: {
      schemaVersion: "authored-export-authority-claim/v2",
      kind: "promotion",
      profileId: "profile:bounded-hybrid",
      profileDefinitionDigest: digest("6"),
      projectionDigest: digest("5"),
      receiptDigests: [digest("1"), digest("2"), digest("3"), digest("4")],
      state: "ready",
    },
  });
  const normalized = normalizeAuthoredReleaseReadinessProjectionV3({ data: serverIssued });
  assert.equal(normalized.releaseProfile.promotionReady, true);
  assert.equal(normalized.releaseProfile.nativeSubsetStatus, "notApplicable");
  assert.deepEqual(normalized.releaseProfile.nativeSubsetCapabilityIds, []);
  assert.equal(normalized.exportAuthorityClaim?.kind, "promotion");
  assert.equal(normalized.releaseProfile.deploymentAuthorized, false);
  assert.equal(normalized.releaseProfile.fullNativeParityStatus, "notApplicable");
  assert.throws(
    () => normalizeAuthoredReleaseReadinessProjectionV3({
      ...serverIssued,
      releaseProfile: { ...serverIssued.releaseProfile, independentVnextGenerationStatus: "ready" },
    }),
    /invalid deprecated readiness status/,
  );
  assert.throws(
    () => normalizeAuthoredReleaseReadinessProjectionV3({
      ...serverIssued,
      releaseProfile: {
        ...serverIssued.releaseProfile,
        allRequiredCapabilityIds: [...serverIssued.releaseProfile.allRequiredCapabilityIds].reverse(),
      },
    }),
    /mismatched releaseProfile\.allRequiredCapabilityIds/,
  );
  assert.throws(
    () => normalizeAuthoredReleaseReadinessProjectionV3({
      ...serverIssued,
      exportAuthorityClaim: undefined,
      releaseProjectionDigest: undefined,
    }),
    /unclaimed release authority pins/,
  );
  assert.throws(
    () => normalizeAuthoredReleaseReadinessProjectionV3({
      ...serverIssued,
      releaseProfile: { ...serverIssued.releaseProfile, nativeSubsetReady: true },
    }),
    /mismatched release readiness receipt pins/,
  );
});

test("v3 fails closed for stale release receipts and unattested promotion", async () => {
  const { normalizeAuthoredReleaseReadinessProjectionV3 } = await loadCapabilityClient();
  const stale = readinessV3({
    capabilities: readinessV3().capabilities.map((capability, index) =>
      index === 0 ? { ...capability, expiresAt: "2020-01-01T00:00:00Z" } : capability,
    ),
  });
  assert.throws(
    () => normalizeAuthoredReleaseReadinessProjectionV3(stale),
    /expired capabilities\.ci\.requiredChecks\.expiresAt/,
  );

  const promotionWithoutAttestation = readinessV3({
    releaseProfile: {
      ...readinessV3().releaseProfile,
      deckEvidenceReady: true,
      serializerProfileReady: true,
      nativeSubsetReady: false,
      releaseCommitReady: true,
      promotionReady: true,
      deckEvidenceReceiptDigests: [digest("2")],
      serializerProfileReceiptDigests: [digest("3"), digest("4")],
      nativeSubsetReceiptDigests: [],
      releaseCommitReceiptDigests: [digest("1")],
      promotionReceiptDigests: [digest("1"), digest("2"), digest("3"), digest("4")],
    },
  });
  assert.throws(
    () => normalizeAuthoredReleaseReadinessProjectionV3(promotionWithoutAttestation),
    /mismatched releaseProfile\.promotionReady/,
  );
});

test("v3 raster-only readiness permits diagnostic Hybrid export before promotion", async () => {
  const { normalizeAuthoredReleaseReadinessProjectionV3 } = await loadCapabilityClient();
  const { authoredHybridUnavailableReason, authoredV3HybridStatusMessage } = await loadCapabilityStatus();
  const projection = normalizeAuthoredReleaseReadinessProjectionV3({ data: readinessV3() });
  assert.equal(
    authoredHybridUnavailableReason({ state: "ready", readiness: { version: "v3", projection } }),
    undefined,
  );
  assert.match(authoredV3HybridStatusMessage(projection), /promotion not ready/);
});
