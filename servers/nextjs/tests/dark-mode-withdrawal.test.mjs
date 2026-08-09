import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (file) => readFileSync(path.join(process.cwd(), file), "utf8");

test("unsupported application dark mode has no runtime declarations", () => {
  const pkg = JSON.parse(read("package.json"));
  assert.equal(pkg.dependencies?.["next-themes"], undefined);
  assert.equal(pkg.devDependencies?.["next-themes"], undefined);
  assert.doesNotMatch(read("tailwind.config.ts"), /\bdarkMode\b/u);
  assert.doesNotMatch(read("app/globals.css"), /^\s*\.dark(?:\s|\{|,)/mu);
  assert.doesNotMatch(read("components/ui/sonner.tsx"), /\.dark\s+\[data-sonner/u);
  assert.doesNotMatch(read("components/ui/chart.tsx"), /(?:THEMES|\.dark\s+\[data-chart|theme:\s*\{)/u);
});

test("documentation distinguishes app appearance from deck themes", () => {
  const documentation = read("../../docs/ui-localization.md");
  assert.match(documentation, /application chrome intentionally supports only its light color scheme/iu);
  assert.match(documentation, /dark \*deck theme\*/iu);
});
