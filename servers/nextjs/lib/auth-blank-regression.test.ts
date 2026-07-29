import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  BLANK_SLIDE_LAYOUT_GROUP,
  BLANK_SLIDE_LAYOUT_ID,
  isTemplateFreePresentation,
} from "../app/(presentation-generator)/_shared/blank-slide.ts";

const nextRoot = fileURLToPath(new URL("../", import.meta.url));

function readSource(relativePath: string) {
  return readFileSync(new URL(relativePath, `file:///${nextRoot.replaceAll("\\", "/")}/`), "utf8");
}

test("AuthGate supports login and first-admin setup with whitespace-safe usernames", () => {
  const source = readSource("components/Auth/AuthGate.tsx");

  assert.match(source, /"\/api\/v1\/auth\/setup"\s*:\s*"\/api\/v1\/auth\/login"/);
  assert.match(source, /event\.target\.value\.replace\(\/\\s\/g,\s*""\)/);
  assert.match(source, /minLength=\{3\}/);
  assert.match(source, /maxLength=\{128\}/);
  assert.match(source, /pattern="\\S\+"/);
  assert.match(source, /required/);
  assert.match(source, /spellCheck=\{false\}/);
  assert.match(source, /minLength=\{isSetupMode \? 8 : 6\}/);
  assert.match(source, /placeholder=\{isSetupMode \? "8자 이상" : "6자 이상"\}/);
  assert.match(source, /관리자 로그인 만들기/);
  assert.match(source, /계속하려면 로그인하세요/);
  assert.match(source, /사용자 이름에는 공백을 사용할 수 없습니다/);
});

test("admin user creation applies the same username constraints", () => {
  const source = readSource(
    "app/(presentation-generator)/(dashboard)/admin/AdminPanel.tsx"
  );

  assert.match(source, /"\/api\/v1\/admin\/users"/);
  assert.match(source, /event\.target\.value\.replace\(\/\\s\/g,\s*""\)/);
  assert.match(source, /minLength=\{3\}/);
  assert.match(source, /maxLength=\{128\}/);
  assert.match(source, /pattern="\\S\+"/);
  assert.match(source, /required/);
  assert.match(source, /spellCheck=\{false\}/);
});

test("template-free presentations cannot open the template slide picker", () => {
  const source = readSource(
    "app/(presentation-generator)/presentation/components/SidePanel.tsx"
  );
  const blankPresentation = {
    slides: [
      {
        layout_group: BLANK_SLIDE_LAYOUT_GROUP,
        layout: BLANK_SLIDE_LAYOUT_ID,
      },
    ],
  };
  const templatedPresentation = {
    slides: [{ layout_group: "general", layout: "general:title" }],
  };

  assert.equal(isTemplateFreePresentation(blankPresentation), true);
  assert.equal(isTemplateFreePresentation(templatedPresentation), false);
  assert.match(
    source,
    /if \(isTemplateFree\) \{[\s\S]*?createBlankPresentationSlide\([\s\S]*?return;[\s\S]*?\}\s*setShowNewSlideSelection\(true\)/
  );
  assert.match(
    source,
    /showNewSlideSelection\s*&&\s*!isTemplateFree\s*&&\s*lastSlideTemplateId/
  );
  assert.doesNotMatch(source, />\s*(?:Blocks|Use Template)\s*</);
});
