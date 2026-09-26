import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";

const sourcePath = fileURLToPath(new URL("./PublicV2OperationsClient.tsx", import.meta.url));
const koCatalogPath = fileURLToPath(new URL("../../../i18n/messages/ko.json", import.meta.url));
const hookPath = fileURLToPath(new URL("../../../lib/public-v2/use-public-v2-operation.ts", import.meta.url));
const dashboardPagePath = fileURLToPath(new URL("../(dashboard)/dashboard/page.tsx", import.meta.url));
const dashboardClientPath = fileURLToPath(new URL("../(dashboard)/dashboard/components/DashboardPage.tsx", import.meta.url));

test("public v2 UI only offers fidelity export and separates terminal states", async () => {
  const source = await readFile(sourcePath, "utf8");
  const ko = JSON.parse(await readFile(koCatalogPath, "utf8")).publicV2;

  assert.match(source, /mode: "fidelity-raster"/);
  assert.doesNotMatch(source, /mode: "editable-hybrid"/);
  assert.match(source, /t\("theOperationWasCanceled"\)/);
  assert.match(source, /t\("theOperationFailed"\)/);
  assert.equal(ko.theOperationWasCanceled, "작업이 취소되었습니다.");
  assert.equal(ko.theOperationFailed, "작업이 실패했습니다.");
  assert.ok(Object.values(ko).some((value) => value.includes("편집할 수 없습니다")));
  assert.match(source, /usePublicV2Operation\(\{ storageScope \}\)/);
  assert.match(source, /submitGenerationRequest/);
  assert.match(source, /submitExport/);
  assert.match(source, /cancel\(\)/);
  assert.match(source, /sourceRevision: snapshot\.sourceRevision/);
  assert.match(source, /t\("theAdmissionResultNeedsToBeRecovered"\)/);
  assert.equal(ko.theAdmissionResultNeedsToBeRecovered, "접수 결과를 복구해야 합니다.");
  assert.match(source, /recoverPendingExport/);
  assert.match(source, /value="registered-template">\{t\("useRegisteredTemplateAsIs"\)\}/);
  assert.match(source, /value="brand-style">\{t\("applyBrandStyle"\)\}/);
  assert.match(source, /value="free-design">\{t\("freeDesign"\)\}/);
  assert.match(source, /value="fidelity-first">\{t\("prioritizeFidelity"\)\}/);
  assert.equal(ko.useRegisteredTemplateAsIs, "등록 템플릿 그대로");
  assert.equal(ko.applyBrandStyle, "브랜드 스타일 적용");
  assert.equal(ko.freeDesign, "자유 디자인");
  assert.equal(ko.prioritizeFidelity, "Fidelity 우선");
  assert.match(source, /value="editable-first" disabled/);
  assert.match(source, /t\("editabilityFirstIsUnavailableUnderThePublic"\)/);
  assert.equal(ko.editabilityFirstIsUnavailableUnderThePublic, "편집 가능성 우선은 Public v2의 Fidelity-only 출력 계약에서 사용할 수 없습니다.");
  assert.match(source, /generationMode: form\.generationMode/);
  assert.match(source, /designPackPin/);
  assert.match(source, /generationRouteReceipt/);
  assert.doesNotMatch(source, /public-v2-generation-engine/);
  assert.doesNotMatch(source, /hyundai-culture-center/);
  assert.match(source, /requestedSlideCount > 100/);
  assert.match(source, /public_v2_slide_count_must_be_between_1_and_100/);
  assert.match(source, /max=\{100\}/);
  assert.doesNotMatch(source, /between_1_and_30|max=\{30\}/);

  const hook = await readFile(hookPath, "utf8");
  assert.match(hook, /visibilitychange/);
  assert.match(hook, /shouldPollPublicV2Operation\(snapshot\)/);
  assert.match(hook, /shouldPollPublicV2Operation\(current\)/);
  assert.match(hook, /shouldPollPublicV2Operation\(latest\)/);

  const dashboardPage = await readFile(dashboardPagePath, "utf8");
  const dashboardClient = await readFile(dashboardClientPath, "utf8");
  assert.match(dashboardPage, /PUBLIC_V2_WEB_CANARY_ENABLED/);
  assert.match(dashboardClient, /public-v2-canary-cta/);
});
