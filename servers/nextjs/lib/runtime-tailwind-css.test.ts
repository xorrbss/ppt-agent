import assert from "node:assert/strict";
import test from "node:test";
import type { Config } from "tailwindcss";
import {
  compileRuntimeTailwindCss,
  normalizeRuntimeTailwindSources,
} from "./runtime-tailwind-css.ts";

const minimalConfig: Config = {
  content: [],
  theme: { extend: {} },
  plugins: [],
};

test("compiles arbitrary utilities used by generated templates", async () => {
  const css = await compileRuntimeTailwindCss(
    ['<div className="bg-[#123456] text-[37px] w-[1280px]" />'],
    minimalConfig
  );

  assert.match(css, /background-color:\s*rgb\(18 52 86/);
  assert.match(css, /font-size:\s*37px/);
  assert.match(css, /width:\s*1280px/);
});

test("validates runtime source payloads", () => {
  assert.deepEqual(normalizeRuntimeTailwindSources(["a", "", null]), ["a"]);
  assert.throws(() => normalizeRuntimeTailwindSources("not-an-array"), TypeError);
});
