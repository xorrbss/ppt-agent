import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { transform } from "esbuild";

const directory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(directory, "authored-emission-capability.ts");

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
      requiredCapabilityIds: promotionCapabilityIds,
      boundedNativeCapabilityIds: releaseCapabilityIds,
      readyReceiptDigests: [digest("a")],
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
  assert.throws(
    () => normalizeAuthoredReleaseReadinessProjectionV2(readinessV2({ capabilities: readinessV2().capabilities.slice(1) })),
    /incomplete capabilities/,
  );
});
