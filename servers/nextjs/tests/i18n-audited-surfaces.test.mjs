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
  "app/(presentation-generator)/presentation/components/KeyboardShortcutsDialog.tsx",
  "app/(presentation-generator)/presentation/components/FlaggedSlideBadge.tsx",
  "app/(presentation-generator)/presentation/components/SidePanel.tsx",
  "app/(presentation-generator)/presentation/components/SlideThumbnailCard.tsx",
  "app/(presentation-generator)/presentation/components/SortableSlide.tsx",
  "app/(presentation-generator)/presentation/workspace/QualityReviewHistoryList.tsx",
  "app/(presentation-generator)/presentation/workspace/WorkspaceGenerationProgress.tsx",
  "app/(presentation-generator)/presentation/components/LoadingState.tsx",
  "app/(presentation-generator)/presentation/components/ConversationResultNotice.tsx",
  "app/(presentation-generator)/presentation/components/ConversationWorkspaceToolbar.tsx",
  "app/(presentation-generator)/presentation/workspace/WorkspaceAttemptNavigation.tsx",
  "app/(presentation-generator)/presentation/components/PresentationMode.tsx",
  "app/(presentation-generator)/presentation/components/header/DashboardNavigationButton.tsx",
  "app/(presentation-generator)/presentation/components/header/MasterLayoutBadge.tsx",
  "app/(presentation-generator)/presentation/workspace/WorkspaceMessage.tsx",
  "app/(presentation-generator)/presentation/workspace/WorkspaceTimelineOperation.tsx",
  "app/(presentation-generator)/presentation/components/header/HybridCoverageDialog.tsx",
  "app/(presentation-generator)/presentation/components/header/HybridExportHistoryDialog.tsx",
  "app/(presentation-generator)/presentation/components/header/ExportMenu.tsx",
  "app/(presentation-generator)/presentation/hooks/useWorkspaceStaging.ts",
  "app/(presentation-generator)/presentation/workspace/WorkspaceChatPanel.tsx",
  "app/(presentation-generator)/presentation/workspace/WorkspaceComposer.tsx",
  "app/(presentation-generator)/presentation/components/PresentationPage.tsx",
  "app/(presentation-generator)/presentation/components/AuthoredElementEditor.tsx",
  "app/(presentation-generator)/presentation/components/AuthoredTemplateChanger.tsx",
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
