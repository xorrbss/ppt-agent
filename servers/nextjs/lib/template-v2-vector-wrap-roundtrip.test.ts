import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  renderTemplateV2GeneralSlideCanvasHtml,
} from "./template-v2-general-renderer.mjs";
import {
  createTemplateV2SlideRenderPlan,
} from "./template-v2-render-plan.mjs";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";
import {
  editTemplateV2VectorPointTopology,
  templateV2ProvenVectorEditCapability,
  templateV2ProvenVectorWrapEditCapability,
} from "./template-v2-vector.ts";

type VectorRoundTripEnvelope = {
  layouts: Array<{
    id: string;
    components: Array<{
      elements: Array<Record<string, unknown>>;
      [key: string]: unknown;
    }>;
    [key: string]: unknown;
  }>;
  future_envelope_field: { retained: boolean };
  [key: string]: unknown;
};

const witness = JSON.parse(
  readFileSync(
    new URL(
      "./fixtures/template-v2-upstream-vector-wrap-roundtrip-57b194b.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  upstream_sha: string;
  import_boundary: string;
  source_fixture: string;
  operation: string;
  document: VectorRoundTripEnvelope;
};

const pinnedUpstreamElements = JSON.parse(
  readFileSync(
    new URL(
      "../../fastapi/tests/fixtures/template_v2/upstream-elements-57b194b.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  upstream_sha: string;
  elements: Array<Record<string, unknown>>;
};

function vectorFrom(envelope: VectorRoundTripEnvelope) {
  return envelope.layouts[0]!.components[0]!.elements[0]!;
}

test("fixed upstream witness proves the Studio wire last-to-first wrap round-trip", () => {
  assert.equal(
    witness.upstream_sha,
    "57b194b234b42c8b28f8a507a30322de200e3e83",
  );
  assert.equal(witness.upstream_sha, pinnedUpstreamElements.upstream_sha);
  assert.equal(
    witness.import_boundary,
    "studio-upstream-wire-compatibility",
  );
  assert.equal(
    witness.source_fixture,
    "servers/fastapi/tests/fixtures/template_v2/upstream-elements-57b194b.json",
  );
  assert.equal(witness.operation, "polygon_add_after_last_wrap");

  const pinnedVector = pinnedUpstreamElements.elements.find(
    (element) => element.type === "vector",
  );
  assert.ok(pinnedVector);
  assert.deepEqual(vectorFrom(witness.document), pinnedVector);

  const source = structuredClone(witness.document);
  const imported = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const edited = structuredClone(
    imported.studioLayouts,
  ) as VectorRoundTripEnvelope;
  const vector = vectorFrom(edited);

  assert.deepEqual(templateV2ProvenVectorWrapEditCapability(vector), {
    kind: "polygon-add-after-last-wrap",
    points: [{ x: 0, y: 0 }, { x: 100, y: 80 }],
  });
  const edit = editTemplateV2VectorPointTopology(vector, {
    type: "add-after",
    index: 1,
  });
  assert.ok(edit);
  assert.equal(edit.selectedIndex, 2);
  assert.deepEqual(edit.points, [
    { x: 0, y: 0 },
    { x: 100, y: 80 },
    { x: 50, y: 40 },
  ]);
  vector.points = edit.points;

  const saved = serializeStudioLayoutsForUpstream(
    imported,
    edited,
  ) as VectorRoundTripEnvelope;
  const savedVector = vectorFrom(saved);
  assert.deepEqual(saved.future_envelope_field, { retained: true });
  assert.deepEqual(
    saved.layouts[0]!.upstream_layout_extension,
    { retained: true },
  );
  assert.equal(savedVector.upstream_element_extension, "vector-vNext");
  assert.deepEqual(savedVector, {
    ...pinnedVector,
    points: [
      { x: 0, y: 0 },
      { x: 100, y: 80 },
      { x: 50, y: 40 },
    ],
  });

  const reloaded = adaptUpstreamTemplateV2LayoutsToStudio(saved);
  const reloadedEnvelope =
    reloaded.studioLayouts as VectorRoundTripEnvelope;
  const reloadedLayout = reloadedEnvelope.layouts[0]!;
  const reloadedVector = vectorFrom(reloadedEnvelope);
  assert.deepEqual(
    serializeStudioLayoutsForUpstream(reloaded),
    saved,
  );
  assert.deepEqual(reloadedVector, savedVector);
  assert.equal(
    templateV2ProvenVectorWrapEditCapability(reloadedVector),
    null,
  );
  assert.equal(templateV2ProvenVectorEditCapability(reloadedVector), null);
  assert.equal(
    editTemplateV2VectorPointTopology(reloadedVector, {
      type: "add-after",
      index: 1,
    }),
    null,
  );

  const plan = createTemplateV2SlideRenderPlan({
    id: reloadedLayout.id,
    ui: reloadedLayout,
  });
  assert.deepEqual(plan.components[0].elements[0].vector, {
    shape: "polygon",
    frame: { x: 0, y: 0, width: 100, height: 80 },
    points: [
      { x: 0, y: 0 },
      { x: 100, y: 80 },
      { x: 50, y: 40 },
    ],
    closed: true,
    tension: null,
    segments: null,
  });
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    id: reloadedLayout.id,
    ui: reloadedLayout,
  });
  assert.match(html, /<polygon points="0,0 100,80 50,40"/);
  assert.deepEqual(source, witness.document);
});

test("unwitnessed finite vector coordinates remain fail-closed", () => {
  const vector = structuredClone(vectorFrom(witness.document));
  vector.points = [
    { x: -Number.MAX_VALUE, y: 0 },
    { x: Number.MAX_VALUE, y: 80 },
  ];

  assert.equal(templateV2ProvenVectorEditCapability(vector), null);
  assert.equal(templateV2ProvenVectorWrapEditCapability(vector), null);
  assert.equal(
    editTemplateV2VectorPointTopology(vector, {
      type: "add-after",
      index: 1,
    }),
    null,
  );
});
