import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const bundled = await build({
  entryPoints: [fileURLToPath(new URL("./canonicalConversationTransport.ts", import.meta.url))],
  bundle: true,
  format: "esm",
  platform: "node",
  write: false,
});
const { canonicalConversationTransport: transport } = await import(
  `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`
);

for (const field of ["failure_class", "failureClass"]) {
  test(`timeline preserves ${field} for generation recovery policy`, async (t) => {
    t.mock.method(globalThis, "fetch", async () => Response.json({ items: [{
      kind: "run", position: 1, run: {
        id: "run-1", operation_type: "presentation.generate", status: "failed", [field]: "internal_defect",
      },
    }] }));
    const timeline = await transport.getTimeline("conversation-1");
    assert.equal(timeline.items[0].run.failureClass, "internal_defect");
  });
}

test("timeline rejects unrecognized failure classes", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ items: [{
    kind: "run", position: 1, run: { id: "run-1", status: "failed", failure_class: "private_diagnostics" },
  }] }));
  const timeline = await transport.getTimeline("conversation-1");
  assert.equal(timeline.items[0].run.failureClass, undefined);
});

for (const nested of [false, true]) {
  test(`stream preserves failure class from ${nested ? "nested run" : "status event"}`, async (t) => {
    const runId = "11111111-1111-4111-8111-111111111111";
    const run = { id: runId, status: "failed", failure_class: "internal_defect" };
    const envelope = {
      schema_version: 1, id: "event-1", type: "run.failed", durability: "durable",
      conversation_id: "22222222-2222-4222-8222-222222222222", run_id: runId,
      cursor: "1", occurred_at: "2026-10-23T00:00:00Z",
      data: { status: "failed", error_code: "internal_defect", ...(nested ? { run } : run) },
    };
    t.mock.method(globalThis, "fetch", async () => new Response(`data: ${JSON.stringify(envelope)}\n\n`));
    const events = [];
    await transport.streamEvents("conversation-1", { onEvent: (event) => events.push(event) });
    assert.equal(events.length, 1);
    assert.equal(events[0].run.failureClass, "internal_defect");
  });
}
