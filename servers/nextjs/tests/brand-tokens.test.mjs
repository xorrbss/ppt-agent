import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const root = process.cwd();
const BRAND_HEX = /#(?:5141e5|5146e5|7a5af8|edeeef|4338ca)(?![0-9a-f])/i;

// Deck/slide content and canvas (Konva) drawing code need literal colours.
const EXEMPT = [
  "app/globals.css",
  "app/(presentation-generator)/presentation/components/presentationFeatureSlots.ts",
  "app/(presentation-generator)/presentation/components/presentationFeatureSlots.test.ts",
  "app/(presentation-generator)/authored-vnext/master-setup/MasterStudioCanvas.cy.tsx",
  "components/canvas-core/CanvasCoreGuides.tsx",
  "app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/constants.ts",
];

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".next")) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (/\.(tsx?|css|mjs)$/.test(entry.name)) yield full;
  }
}

test("app/ and components/ use brand tokens instead of raw brand hex literals", () => {
  const offenders = [];
  for (const dir of ["app", "components"]) {
    for (const file of walk(path.join(root, dir))) {
      const rel = path.relative(root, file).split(path.sep).join("/");
      if (EXEMPT.includes(rel)) continue;
      readFileSync(file, "utf8")
        .split(/\r?\n/)
        .forEach((line, index) => {
          if (BRAND_HEX.test(line)) offenders.push(`${rel}:${index + 1}`);
        });
    }
  }
  assert.deepEqual(offenders, [], "use bg-brand / text-brand-accent / border-surface (see tailwind.config.ts)");
});
