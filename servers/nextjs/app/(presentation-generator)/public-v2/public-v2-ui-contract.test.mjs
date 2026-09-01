import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";

const sourcePath = fileURLToPath(new URL("./PublicV2OperationsClient.tsx", import.meta.url));
const hookPath = fileURLToPath(new URL("../../../lib/public-v2/use-public-v2-operation.ts", import.meta.url));
const dashboardPagePath = fileURLToPath(new URL("../(dashboard)/dashboard/page.tsx", import.meta.url));
const dashboardClientPath = fileURLToPath(new URL("../(dashboard)/dashboard/components/DashboardPage.tsx", import.meta.url));

test("public v2 UI only offers fidelity export and separates terminal states", async () => {
  const source = await readFile(sourcePath, "utf8");

  assert.match(source, /mode: "fidelity-raster"/);
  assert.doesNotMatch(source, /mode: "editable-hybrid"/);
  assert.match(source, /작업이 취소되었습니다/);
  assert.match(source, /작업이 실패했습니다/);
  assert.match(source, /편집할 수 없습니다/);
  assert.match(source, /usePublicV2Operation\(\{ storageScope \}\)/);
  assert.match(source, /submitGenerationRequest/);
  assert.match(source, /submitExport/);
  assert.match(source, /cancel\(\)/);
  assert.match(source, /sourceRevision: snapshot\.sourceRevision/);
  assert.match(source, /접수 결과를 복구해야 합니다/);
  assert.match(source, /recoverPendingExport/);
  assert.match(source, /requestedSlideCount > 100/);
  assert.match(source, /public_v2_slide_count_must_be_between_1_and_100/);
  assert.match(source, /max=\{100\}/);
  assert.doesNotMatch(source, /between_1_and_30|max=\{30\}/);

  const hook = await readFile(hookPath, "utf8");
  assert.match(hook, /visibilitychange/);
  assert.match(hook, /snapshot\.status === "queued"/);

  const dashboardPage = await readFile(dashboardPagePath, "utf8");
  const dashboardClient = await readFile(dashboardClientPath, "utf8");
  assert.match(dashboardPage, /PUBLIC_V2_WEB_CANARY_ENABLED/);
  assert.match(dashboardClient, /public-v2-canary-cta/);
});
