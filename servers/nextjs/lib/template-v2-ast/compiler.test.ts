import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  compileTemplateRenderPlan,
  materializeTemplateRenderPlan,
  TemplateV2CompilerError,
} from "./compiler.ts";
import type { CanonicalTemplateAst } from "./template-v2-ast.generated.ts";
import { templateV2UiToHtmlFragment } from "../template-v2-json-to-html.ts";
import { createTemplateV2SlideRenderPlan } from "../template-v2-render-plan.mjs";

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
  assert.deepEqual(
    (flexChildren as Array<Record<string, unknown>>).map((item) =>
      (((item.children as Array<Record<string, unknown>>)[0].runs as Array<{ text: string }>)[0].text)
    ),
    ["A", "B"],
  );
  assert.deepEqual(
    positioned.slice(1).map((item) =>
      (((item.children as Array<Record<string, unknown>>)[0].runs as Array<{ text: string }>)[0].text)
    ),
    ["One", "Two"],
  );

  const renderable = structuredClone(rendered);
  (renderable.components as Array<Record<string, unknown>>)[1].elements = positioned.slice(1);
  const geometryPlan = createTemplateV2SlideRenderPlan({ ui: renderable });
  assert.equal(geometryPlan.components[0].elements[0].children.length, 2);
  assert.deepEqual(
    geometryPlan.components[1].elements.map((item) => item.frame.x),
    [320, 120],
  );
  const html = templateV2UiToHtmlFragment(renderable);
  assert.match(html ?? "", /First card|>A</);
  assert.match(html ?? "", />One</);
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
