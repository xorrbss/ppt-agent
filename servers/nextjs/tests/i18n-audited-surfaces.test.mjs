import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { collectHardcodedUiText } from "../scripts/check-i18n-hardcoding.mjs";

const root = process.cwd();
const auditedFiles = [
  "app/(presentation-generator)/components/DevaxWorkspaceShell.tsx",
  "app/(presentation-generator)/upload/components/UploadDesignGallery.tsx",
  "app/not-found.tsx",
  "app/global-error.tsx",
  "app/error.tsx",
  "app/ConfigurationInitializer.tsx",
  "app/(export)/pdf-maker/page.tsx",
  "app/(presentation-generator)/(dashboard)/admin/AdminPanel.tsx",
  "app/(presentation-generator)/(dashboard)/settings/UserAccountSettings.tsx",
  "app/(presentation-generator)/(dashboard)/dashboard/components/SharePopover.tsx",
  "app/(presentation-generator)/upload/components/AdvanceSettings.tsx",
  "app/(presentation-generator)/outline/components/MasterSelectionSummary.tsx",
  "app/(presentation-generator)/outline/components/OutlinePage.tsx",
  "app/(presentation-generator)/outline/components/TemplateSelection.tsx",
  "app/(presentation-generator)/public-v2/PublicV2OperationsClient.tsx",
  "app/(presentation-generator)/upload/components/TemplateCatalog.tsx",
  "app/(presentation-generator)/upload/components/UploadPage.tsx",
  "app/(presentation-generator)/upload/components/upload-page/GenerationStrategySelector.tsx",
  "app/(presentation-generator)/upload/components/upload-page/PreviousAttemptNotice.tsx",
  "app/(presentation-generator)/upload/components/upload-page/RasterSourceReview.tsx",
  "app/(presentation-generator)/upload/components/upload-page/SourceContentLossDialog.tsx",
  "app/(presentation-generator)/upload/components/upload-page/SourceContentSummaryPanel.tsx",
  "app/(presentation-generator)/upload/components/upload-page/createRasterRedesignStarter.ts",
  "app/(presentation-generator)/upload/components/upload-page/raster-review-recovery.ts",
  "app/(presentation-generator)/upload/components/upload-page/uploadGenerationHandlers.ts",
  "app/(presentation-generator)/upload/components/upload-page/useMasterCatalogController.ts",
  "app/(presentation-generator)/upload/components/upload-page/useRasterReviewActions.ts",
  "app/(presentation-generator)/upload/components/upload-page/useRasterSessionRecovery.ts",
  "app/(presentation-generator)/upload/components/upload-page/useRasterSourceReviewState.tsx",
  "app/(presentation-generator)/upload/components/upload-page/useSourceContentAdmission.ts",
  "app/(presentation-generator)/presentation/components/KeyboardShortcutsDialog.tsx",
  "app/(presentation-generator)/presentation/components/FlaggedSlideBadge.tsx",
  "app/(presentation-generator)/presentation/components/SidePanel.tsx",
  "app/(presentation-generator)/presentation/components/SlideThumbnailCard.tsx",
  "app/(presentation-generator)/presentation/components/SortableSlide.tsx",
  "components/slide-editor/shortcuts/ShortcutKeys.tsx",
  "components/slide-editor/shortcuts/editorShortcuts.ts",
];

test("the audited surfaces contain no hardcoded user-facing copy", () => {
  const files = [...auditedFiles];
  const findings = files.flatMap((file) =>
    collectHardcodedUiText(readFileSync(path.join(root, file), "utf8"), file),
  );
  assert.deepEqual(
    findings.map(({ file, kind, line, text }) => `${file}:${line} [${kind}] ${JSON.stringify(text)}`),
    [],
  );
});
