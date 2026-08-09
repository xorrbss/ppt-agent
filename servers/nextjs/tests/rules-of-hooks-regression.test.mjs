import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { ESLint } from "eslint";

const NEXTJS_ROOT = fileURLToPath(new URL("..", import.meta.url));
const REGRESSION_FILES = [
  "app/(export)/pdf-maker/PdfMakerPage.tsx",
  "app/(presentation-generator)/(dashboard)/dashboard/components/Header.tsx",
  "app/(presentation-generator)/(dashboard)/settings/SettingPage.tsx",
  "app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/ThemeCard.tsx",
  "app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/index.tsx",
  "app/(presentation-generator)/components/PresentationMode.tsx",
  "app/(presentation-generator)/custom-template/CustomTemplatePage.tsx",
  "app/(presentation-generator)/presentation/hooks/usePresentationData.ts",
  "app/(presentation-generator)/presentation/page.tsx",
  "app/(presentation-generator)/presentation/utils/applyPresentationThemeDom.ts",
  "app/hooks/compileLayout.ts",
  "app/schema/page.tsx",
];

test("rules-of-hooks stays enabled and the repaired call sites remain valid", async () => {
  const eslint = new ESLint({ cwd: NEXTJS_ROOT });
  const config = await eslint.calculateConfigForFile(REGRESSION_FILES[0]);

  assert.equal(config.rules["react-hooks/rules-of-hooks"]?.[0], 2);

  const results = await eslint.lintFiles(REGRESSION_FILES);
  const violations = results.flatMap((result) =>
    result.messages
      .filter((message) => message.ruleId === "react-hooks/rules-of-hooks")
      .map((message) => `${result.filePath}:${message.line}:${message.column} ${message.message}`)
  );
  assert.deepEqual(violations, []);

  const inlineDisables = [];
  await Promise.all(
    REGRESSION_FILES.map(async (file) => {
      const source = await readFile(new URL(`../${file}`, import.meta.url), "utf8");
      if (/eslint-disable[^\n]*react-hooks\/rules-of-hooks/.test(source)) {
        inlineDisables.push(file);
      }
    })
  );
  assert.deepEqual(inlineDisables, []);
});
