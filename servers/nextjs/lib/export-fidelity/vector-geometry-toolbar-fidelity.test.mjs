import assert from "node:assert/strict";
import test from "node:test";

import { renderTemplateV2GeneralPresentationHtml } from "../template-v2-general-renderer.mjs";
import { createTemplateV2SlideRenderPlan } from "../template-v2-render-plan.mjs";
import {
  projectTemplateV2ElementForRenderPlan,
  templateV2EditableVectorGeometry,
  templateV2VectorRenderGeometry,
} from "../template-v2-vector.ts";

function slideWith(vector) {
  return {
    id: "vector-geometry-fidelity",
    ui: {
      components: [{
        id: "body",
        position: { x: 0, y: 0 },
        elements: [vector],
      }],
    },
  };
}

function presentationWith(vector) {
  return {
    version: "v2-standard",
    mode: "template",
    slides: [slideWith(vector)],
  };
}

function applyAtPath(root, path, value) {
  const result = { ...root };
  let cursor = result;
  path.forEach((segment, index) => {
    if (index === path.length - 1) {
      cursor[segment] = value;
      return;
    }
    const current = cursor[segment];
    const next = Array.isArray(current)
      ? [...current]
      : current && typeof current === "object"
        ? { ...current }
        : {};
    cursor[segment] = next;
    cursor = next;
  });
  return result;
}

function parseSvgPoints(html, tag) {
  const value = html.match(new RegExp(`<${tag} points="([^"]+)"`))?.[1];
  assert.ok(value, `${tag} points are present`);
  return value.split(" ").map((pair) => {
    const [x, y] = pair.split(",").map(Number);
    return { x, y };
  });
}

function assertPointsClose(actual, expected, tolerance = 1e-10) {
  assert.equal(actual.length, expected.length);
  actual.forEach((point, index) => {
    assert.ok(Math.abs(point.x - expected[index].x) <= tolerance);
    assert.ok(Math.abs(point.y - expected[index].y) <= tolerance);
  });
}

test("leaf geometry edits preserve unknown JSON and match Konva and PPT geometry", () => {
  const authored = {
    type: "vector",
    shape: "polygon",
    points: [
      { x: 40, y: 30, future_point: { pressure: 0.75 } },
      { x: 210, y: 45, future_point: { pressure: 0.5 } },
      { x: 185, y: 180 },
      { x: 25, y: 145 },
    ],
    closed: true,
    corner_radii: [14, 8, 11, 6],
    curve: {
      type: "smooth",
      tension: 0.35,
      segments: 8,
      future_curve: { interpolation: "v3" },
    },
    fill: { color: "#123456" },
    stroke: { color: "#abcdef", width: 3 },
    future_vector: { retained: true },
  };

  const edited = applyAtPath(
    applyAtPath(
      applyAtPath(
        applyAtPath(authored, ["points", 0, "x"], 52.25),
        ["corner_radii", 0],
        18
      ),
      ["curve", "tension"],
      0.62
    ),
    ["curve", "segments"],
    12
  );
  const persisted = JSON.parse(JSON.stringify(edited));

  assert.deepEqual(persisted.future_vector, authored.future_vector);
  assert.deepEqual(persisted.points[0].future_point, authored.points[0].future_point);
  assert.deepEqual(persisted.curve.future_curve, authored.curve.future_curve);
  assert.equal(persisted.points[0].x, 52.25);
  assert.deepEqual(persisted.corner_radii, [18, 8, 11, 6]);
  assert.equal(persisted.curve.tension, 0.62);
  assert.equal(persisted.curve.segments, 12);
  assert.deepEqual(templateV2EditableVectorGeometry(persisted), {
    shape: "polygon",
    closed: true,
    points: [
      { x: 52.25, y: 30 },
      { x: 210, y: 45 },
      { x: 185, y: 180 },
      { x: 25, y: 145 },
    ],
    curve: { tension: 0.62, segments: 12, canRemove: false },
    cornerRadii: [18, 8, 11, 6],
  });

  const projected = projectTemplateV2ElementForRenderPlan(persisted);
  const plan = createTemplateV2SlideRenderPlan(slideWith(projected));
  const vectorPlan = plan.components[0].elements[0].vector;
  assert.ok(vectorPlan);
  const konvaGeometry = templateV2VectorRenderGeometry(persisted, vectorPlan);

  const pptHtml = renderTemplateV2GeneralPresentationHtml(
    presentationWith(persisted)
  );
  const pptPoints = parseSvgPoints(pptHtml, "polygon");
  assertPointsClose(pptPoints, konvaGeometry.points);
  assert.ok(pptPoints.length > persisted.points.length);
});

test("canonical curve lifecycle and explicit two-point shape conversion are lossless", () => {
  const authored = {
    type: "vector",
    shape: "polygon",
    points: [
      { x: 10, y: 20, future_point: { retained: true } },
      { x: 130, y: 90 },
    ],
    closed: false,
    curve: null,
    stroke: { color: "#001122", width: 2 },
    future_vector: { version: 3 },
  };
  const curved = JSON.parse(JSON.stringify(applyAtPath(
    authored,
    ["curve"],
    { type: "smooth", tension: 0.5, segments: 16 }
  )));
  assert.deepEqual(templateV2EditableVectorGeometry(curved)?.curve, {
    tension: 0.5,
    segments: 16,
    canRemove: true,
  });
  assert.deepEqual(curved.points[0].future_point, { retained: true });
  assert.deepEqual(curved.future_vector, { version: 3 });

  const uncurved = applyAtPath(curved, ["curve"], null);
  const ellipse = JSON.parse(JSON.stringify(
    applyAtPath(uncurved, ["shape"], "ellipse")
  ));
  assert.equal(templateV2EditableVectorGeometry(ellipse)?.shape, "ellipse");
  assert.deepEqual(ellipse.points[0].future_point, { retained: true });
  assert.deepEqual(ellipse.future_vector, { version: 3 });

  const plan = createTemplateV2SlideRenderPlan(slideWith(ellipse));
  assert.equal(plan.components[0].elements[0].vector?.shape, "ellipse");
  assert.match(
    renderTemplateV2GeneralPresentationHtml(presentationWith(ellipse)),
    /<ellipse /
  );
});

test("open path is represented consistently and does not acquire a fill", () => {
  const vector = {
    type: "vector",
    points: [
      { x: 10, y: 20 },
      { x: 90, y: 5 },
      { x: 140, y: 80 },
    ],
    closed: false,
    curve: { type: "smooth", tension: 0.45, segments: 7 },
    fill: { color: "#ff0000" },
    stroke: { color: "#001122", width: 2 },
  };
  const plan = createTemplateV2SlideRenderPlan(slideWith(vector));
  const vectorPlan = plan.components[0].elements[0].vector;
  assert.ok(vectorPlan);
  assert.equal(vectorPlan.closed, false);

  const konvaGeometry = templateV2VectorRenderGeometry(vector, vectorPlan);
  const pptHtml = renderTemplateV2GeneralPresentationHtml(
    presentationWith(vector)
  );
  assert.doesNotMatch(pptHtml, /<polygon points=/);
  assert.match(pptHtml, /<polyline points="[^"]+" fill="none"/);
  assertPointsClose(parseSvgPoints(pptHtml, "polyline"), konvaGeometry.points);
});

test("editable geometry capability fails closed at renderer contract boundaries", () => {
  const valid = {
    type: "vector",
    points: [{ x: 0, y: 0 }, { x: 10, y: 10 }],
    stroke: { color: "#000000", width: 1 },
  };
  assert.deepEqual(templateV2EditableVectorGeometry(valid), {
    shape: null,
    closed: false,
    points: [{ x: 0, y: 0 }, { x: 10, y: 10 }],
    curve: null,
    cornerRadii: [],
  });
  assert.deepEqual(
    templateV2EditableVectorGeometry({
      ...valid,
      curve: { type: "smooth", future_curve: true },
    }),
    {
      shape: null,
      closed: false,
      points: [{ x: 0, y: 0 }, { x: 10, y: 10 }],
      curve: { tension: 0.5, segments: 16, canRemove: false },
      cornerRadii: [],
    }
  );
  assert.deepEqual(
    templateV2EditableVectorGeometry({
      ...valid,
      shape: "polygon",
      closed: true,
      cornerRadii: [4, 3],
    }),
    {
      shape: "polygon",
      closed: true,
      points: [{ x: 0, y: 0 }, { x: 10, y: 10 }],
      curve: null,
      cornerRadii: null,
    }
  );

  const invalidCases = [
    { ...valid, points: [{ x: 0, y: 0 }] },
    { ...valid, points: [{ x: 0, y: Number.NaN }, { x: 1, y: 1 }] },
    { ...valid, shape: "triangle" },
    { ...valid, shape: "ellipse", points: [...valid.points, { x: 20, y: 20 }] },
    { ...valid, closed: "false" },
    { ...valid, corner_radii: [1, 2, 3] },
    { ...valid, corner_radii: [1, -2] },
    { ...valid, corner_radii: [1, 10001] },
    { ...valid, curve: { type: "bezier", tension: 0.5, segments: 8 } },
    { ...valid, curve: { type: "smooth", tension: 1.1, segments: 8 } },
    { ...valid, curve: { type: "smooth", tension: 0.5, segments: 8.5 } },
    { ...valid, curve: { type: "smooth", tension: 0.5, segments: 97 } },
  ];
  invalidCases.forEach((value) => {
    assert.equal(templateV2EditableVectorGeometry(value), null);
  });

  assert.throws(
    () =>
      renderTemplateV2GeneralPresentationHtml(
        presentationWith({
          ...valid,
          curve: { type: "smooth", tension: 0.5, segments: 8.5 },
        })
      ),
    /template_v2_renderer_invalid_vector_segments/
  );
});
