import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  compileTemplateRenderPlan,
  materializeTemplateRenderPlan,
  TemplateV2CompilerError,
} from "./compiler.ts";
import type { CanonicalTemplateAst } from "./template-v2-ast.generated.ts";

const ast = JSON.parse(
  readFileSync(
    new URL(
      "../../../../contracts/template_v2/fixtures/repeatable-grouped-content.a3352f3c.ast.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as CanonicalTemplateAst;

const content = {
  regular_cards: { cards: [{ card_title: "A" }, { card_title: "B" }] },
  positioned_callouts: { items: [{ item_title: "One" }, { item_title: "Two" }] },
};

test("TS editor/HTML lowering consumes the canonical Python AST", () => {
  const plan = compileTemplateRenderPlan(ast, "canonical_repeat_fixture", content);
  const rendered = materializeTemplateRenderPlan(plan);
  const components = rendered.components as Array<Record<string, unknown>>;
  const regularElements = components[0].elements as Array<Record<string, unknown>>;
  const flexChildren = regularElements[0].children as unknown[];
  const positioned = components[1].elements as Array<Record<string, unknown>>;

  assert.equal(flexChildren.length, 2);
  assert.equal(positioned.length, 3);
  assert.deepEqual(
    positioned.slice(1).map((item) => (item.position as { x: number }).x),
    [320, 120],
  );
});

test("unsupported or future AST fails closed", () => {
  const unsupported = structuredClone(ast);
  unsupported.capabilities.review_required = true;
  assert.throws(
    () => compileTemplateRenderPlan(unsupported, "canonical_repeat_fixture", content),
    (error) =>
      error instanceof TemplateV2CompilerError &&
      error.code === "template_ast_review_required",
  );
});
