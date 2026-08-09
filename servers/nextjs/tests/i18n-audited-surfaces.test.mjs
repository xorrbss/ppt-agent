import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { collectHardcodedUiText } from "../scripts/check-i18n-hardcoding.mjs";

const root = process.cwd();
const auditedFiles = [
  "app/not-found.tsx",
  "app/global-error.tsx",
  "app/error.tsx",
  "app/ConfigurationInitializer.tsx",
  "app/(export)/pdf-maker/page.tsx",
  "app/(presentation-generator)/(dashboard)/admin/AdminPanel.tsx",
  "app/(presentation-generator)/(dashboard)/settings/UserAccountSettings.tsx",
  "app/(presentation-generator)/(dashboard)/dashboard/components/SharePopover.tsx",
  "app/(presentation-generator)/components/EditableLayoutWrapper.tsx",
  "app/(presentation-generator)/components/ImageEditor.tsx",
  "app/(presentation-generator)/components/IconsEditor.tsx",
  "app/(presentation-generator)/presentation/components/KeyboardShortcutsDialog.tsx",
  "app/(presentation-generator)/presentation/components/SidePanel.tsx",
  "app/(presentation-generator)/presentation/components/SlideThumbnailCard.tsx",
  "app/(presentation-generator)/presentation/components/SortableSlide.tsx",
  "components/slide-editor/shortcuts/ShortcutKeys.tsx",
  "components/slide-editor/shortcuts/editorShortcuts.ts",
];

function collectStudioFiles(directory, result = []) {
  for (const entry of readdirSync(path.join(root, directory), { withFileTypes: true })) {
    const relative = path.posix.join(directory.replaceAll("\\", "/"), entry.name);
    if (entry.isDirectory()) collectStudioFiles(relative, result);
    else if (/\.(?:ts|tsx)$/u.test(entry.name) && !/\.(?:cy|test)\./u.test(entry.name)) result.push(relative);
  }
  return result;
}

test("the audited surfaces contain no hardcoded user-facing copy", () => {
  const files = [
    ...auditedFiles,
    ...collectStudioFiles("app/template-v2-studio/[templateId]"),
  ];
  const findings = files.flatMap((file) =>
    collectHardcodedUiText(readFileSync(path.join(root, file), "utf8"), file),
  );
  assert.deepEqual(
    findings.map(({ file, kind, line, text }) => `${file}:${line} [${kind}] ${JSON.stringify(text)}`),
    [],
  );
});
