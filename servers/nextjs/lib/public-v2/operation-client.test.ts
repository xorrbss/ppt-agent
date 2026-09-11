import assert from "node:assert/strict";
import test from "node:test";

import {
  createPublicV2OperationClient,
  type PublicV2OperationClientOptions,
} from "./operation-client.ts";

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number { return this.values.size; }
  clear(): void { this.values.clear(); }
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  key(index: number): string | null { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string): void { this.values.delete(key); }
  setItem(key: string, value: string): void { this.values.set(key, value); }
}

const operationId = "op_public_1";
const presentationId = "prs_public_1";

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function status(state: "queued" | "running" | "succeeded" | "failed" | "cancelled" = "running") {
  return {
    schemaVersion: "operation-status/v2",
    operationId,
    presentationId,
    kind: "presentation.generate",
    status: state,
    phase: state === "running" ? "authoring" : "publishing",
    progressPercent: state === "running" ? 25 : 100,
    acceptedAt: "2026-08-30T00:00:00Z",
    updatedAt: "2026-08-30T00:00:01Z",
    absoluteDeadlineAt: "2026-08-30T00:05:00Z",
    error: state === "failed" || state === "cancelled"
      ? { reasonCode: "operation_cancelled", retryable: false }
      : null,
    releaseReadiness: "not_assessed",
    readinessScope: "artifact:fidelity",
    fidelityArtifactReady: false,
    nativeEditabilityReady: false,
    promotionReady: false,
    deploymentAuthorized: false,
    artifacts: [],
  };
}

function options(
  storage: Storage,
  fetchImpl: typeof fetch,
): PublicV2OperationClientOptions {
  return {
    storage,
    storageKey: "test.public-v2.operation",
    createIdempotencyKey: () => "public-v2:test-key",
    fetchImpl,
  };
}

test("generation keeps only an opaque durable idempotency checkpoint, then resumes from its event cursor", async () => {
  const storage = new MemoryStorage();
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    if (url === "/api/v2/presentations/generations") {
      return json({
        schemaVersion: "operation-receipt/v2",
        operationId,
        receiptId: "rcpt_public_1",
        presentationId,
        status: "queued",
      }, 202);
    }
    if (url === `/api/v2/operations/${operationId}`) return json(status());
    if (url === `/api/v2/operations/${operationId}/events?limit=200`) {
      return json({
        schemaVersion: "operation-event-page/v2",
        operationId,
        events: [{
          schemaVersion: "operation-event/v2",
          eventId: "evt_17",
          operationId,
          sequence: 1,
          occurredAt: "2026-08-30T00:00:01Z",
          status: "running",
          phase: "authoring",
          progressPercent: 25,
        }],
        nextCursor: "17",
      });
    }
    if (url === `/api/v2/operations/${operationId}/events?limit=200&after=17`) {
      return json({
        schemaVersion: "operation-event-page/v2",
        operationId,
        events: [],
        nextCursor: null,
      });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  const client = createPublicV2OperationClient(options(storage, fetchImpl));
  await client.submitGeneration({ content: "private content must not be persisted" });

  assert.equal(calls[0].init?.headers instanceof Headers ? calls[0].init.headers.get("Idempotency-Key") : (calls[0].init?.headers as Record<string, string>)?.["Idempotency-Key"], "public-v2:test-key");
  const persisted = storage.getItem("test.public-v2.operation") ?? "";
  assert.match(persisted, /public-v2:test-key/);
  assert.doesNotMatch(persisted, /private content/);
  assert.match(persisted, /"eventCursor":"17"/);
  assert.equal(client.snapshot.eventCursor, "17");

  const resumed = createPublicV2OperationClient(options(storage, fetchImpl));
  await resumed.resume();
  assert.equal(resumed.snapshot.events.length, 0);
  assert.equal(resumed.snapshot.eventCursor, "17");
  assert.match(storage.getItem("test.public-v2.operation") ?? "", /"eventCursor":"17"/);
  assert.ok(calls.some((call) => call.url.endsWith("events?limit=200&after=17")));
});

test("cancel remains separate from a terminal failure and reuses the persisted operation", async () => {
  const storage = new MemoryStorage();
  storage.setItem("test.public-v2.operation", JSON.stringify({
    version: 1,
    kind: "presentation.generate",
    idempotencyKey: "public-v2:existing",
    requestFingerprint: "a".repeat(64),
    presentationId,
    operationId,
    eventCursor: "17",
  }));
  const calls: string[] = [];
  let cancelled = false;
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = String(input);
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (url === `/api/v2/operations/${operationId}/cancel`) {
      cancelled = true;
      return json({
        schemaVersion: "operation-cancel-receipt/v2",
        operationId,
        status: "running",
        cancellationRequested: true,
        requestedAt: "2026-08-30T00:00:02Z",
      }, 202);
    }
    if (url === `/api/v2/operations/${operationId}`) return json(status(cancelled ? "cancelled" : "running"));
    if (url === `/api/v2/operations/${operationId}/events?limit=200&after=17`) {
      return json({ schemaVersion: "operation-event-page/v2", operationId, events: [], nextCursor: null });
    }
    if (url === `/api/v2/operations/${operationId}/events?limit=200`) {
      return json({ schemaVersion: "operation-event-page/v2", operationId, events: [], nextCursor: null });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  const client = createPublicV2OperationClient(options(storage, fetchImpl));
  await client.resume();
  await client.cancel();

  assert.ok(calls.includes(`POST /api/v2/operations/${operationId}/cancel`));
  assert.equal(client.snapshot.lifecycle, "cancelled");
  assert.equal(client.snapshot.failure?.reasonCode, "operation_cancelled");
  assert.equal(client.snapshot.canCancel, false);
});

test("generation source revision drives fidelity export status and authorized download", async () => {
  const storage = new MemoryStorage();
  const exportOperationId = "op_export_1";
  const artifactId = "artifact_export_1";
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let exportAttempts = 0;
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    if (url === "/api/v2/presentations/generations") {
      return json({
        schemaVersion: "operation-receipt/v2",
        operationId,
        receiptId: "rcpt_generation_1",
        presentationId,
        status: "queued",
      }, 202);
    }
    if (url === `/api/v2/operations/${operationId}`) {
      return json({
        ...status("succeeded"),
        sourceRevision: 7,
      });
    }
    if (url === `/api/v2/operations/${operationId}/events?limit=200`) {
      return json({ schemaVersion: "operation-event-page/v2", operationId, events: [], nextCursor: null });
    }
    if (url === `/api/v2/presentations/${presentationId}/exports`) {
      assert.deepEqual(JSON.parse(String(init?.body)), {
        schemaVersion: "presentation-export-request/v2",
        qualityPolicy: "release-v1",
        sourceRevision: 7,
        output: {
          format: "pptx",
          mode: "fidelity-raster",
          fontEmbedding: "none",
        },
      });
      exportAttempts += 1;
      if (exportAttempts === 1) throw new TypeError("export receipt lost");
      return json({
        schemaVersion: "operation-receipt/v2",
        operationId: exportOperationId,
        receiptId: "rcpt_export_1",
        presentationId,
        status: "queued",
      }, 202);
    }
    if (url === `/api/v2/operations/${exportOperationId}`) {
      return json({
        schemaVersion: "operation-status/v2",
        operationId: exportOperationId,
        presentationId,
        kind: "presentation.export",
        status: "succeeded",
        phase: "publishing",
        progressPercent: 100,
        sourceRevision: 7,
        acceptedAt: "2026-08-30T00:00:00Z",
        updatedAt: "2026-08-30T00:00:03Z",
        absoluteDeadlineAt: "2026-08-30T00:05:00Z",
        error: null,
        releaseReadiness: "not_assessed",
        readinessScope: "artifact:fidelity",
        fidelityArtifactReady: false,
        nativeEditabilityReady: false,
        promotionReady: false,
        deploymentAuthorized: false,
        artifacts: [{
          artifactId,
          operationId: exportOperationId,
          presentationId,
          kind: "presentation-pptx",
          digest: `sha256:${"a".repeat(64)}`,
          sizeBytes: 1024,
          mediaType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          download: `/api/v2/artifacts/${artifactId}/download`,
        }],
      });
    }
    if (url === `/api/v2/operations/${exportOperationId}/events?limit=200`) {
      return json({ schemaVersion: "operation-event-page/v2", operationId: exportOperationId, events: [], nextCursor: null });
    }
    if (url === `/api/v2/artifacts/${artifactId}/download`) {
      return new Response(null, {
        status: 303,
        headers: { location: "https://download.example.test/export" },
      });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  const client = createPublicV2OperationClient(options(storage, fetchImpl));
  await client.submitGeneration({ content: "public generation" });
  assert.equal(client.snapshot.sourceRevision, 7);
  await assert.rejects(client.submitExport(presentationId, {
    sourceRevision: client.snapshot.sourceRevision ?? -1,
    output: { format: "pptx", mode: "fidelity-raster" },
  }));
  const recovered = createPublicV2OperationClient(options(storage, fetchImpl));
  await recovered.resume();
  assert.equal(recovered.snapshot.pendingAdmission, true);
  assert.equal(recovered.snapshot.kind, "presentation.export");
  await recovered.recoverPendingExport();
  assert.equal(exportAttempts, 2);
  assert.equal(recovered.snapshot.status, "succeeded");
  assert.equal(recovered.snapshot.artifacts.length, 1);
  const artifact = recovered.snapshot.artifacts[0];
  assert.ok(artifact);
  const download = await fetchImpl(
    recovered.artifactDownloadUrl(artifact),
    { redirect: "manual", credentials: "include" },
  );
  assert.equal(download.status, 303);
  assert.equal(download.headers.get("location"), "https://download.example.test/export");
  assert.ok(calls.some((call) => call.url === `/api/v2/presentations/${presentationId}/exports`));
});

test("uncertain admission requires an exact replay or an explicit local abandon", async () => {
  const storage = new MemoryStorage();
  let admissionAttempts = 0;
  const fetchImpl: typeof fetch = async (input) => {
    const url = String(input);
    if (url === "/api/v2/presentations/generations") {
      admissionAttempts += 1;
      if (admissionAttempts === 1) throw new TypeError("connection lost");
      return json({
        schemaVersion: "operation-receipt/v2",
        operationId,
        receiptId: "rcpt_recovered_1",
        presentationId,
        status: "queued",
      }, 202);
    }
    if (url === `/api/v2/operations/${operationId}`) return json(status());
    if (url === `/api/v2/operations/${operationId}/events?limit=200`) {
      return json({ schemaVersion: "operation-event-page/v2", operationId, events: [], nextCursor: null });
    }
    throw new Error(`unexpected URL: ${url}`);
  };

  const first = createPublicV2OperationClient(options(storage, fetchImpl));
  await assert.rejects(first.submitGeneration({ content: "recover me" }));
  const resumed = createPublicV2OperationClient(options(storage, fetchImpl));
  await resumed.resume();
  assert.equal(resumed.snapshot.pendingAdmission, true);
  assert.equal(resumed.snapshot.lifecycle, "pending_admission");
  await assert.rejects(
    resumed.submitGeneration({ content: "different" }),
    (error: unknown) => error instanceof Error && error.message === "public_v2_pending_request_mismatch",
  );
  await resumed.submitGeneration({ content: "recover me" });
  assert.equal(admissionAttempts, 2);
  assert.equal(resumed.snapshot.operationId, operationId);
  assert.equal(resumed.snapshot.pendingAdmission, false);
});

test("default browser checkpoint rotates when the server principal scope changes", () => {
  const storage = new MemoryStorage();
  const fetchImpl: typeof fetch = async () => { throw new Error("not called"); };
  createPublicV2OperationClient({ storage, storageScope: "principal-a", fetchImpl });
  storage.setItem("presenton.public-v2.operation/v1:principal-a", "opaque-checkpoint");
  createPublicV2OperationClient({ storage, storageScope: "principal-b", fetchImpl });
  assert.equal(storage.getItem("presenton.public-v2.operation/v1:principal-a"), null);
  assert.equal(
    storage.getItem("presenton.public-v2.active-principal/v1"),
    "principal-b",
  );
});
