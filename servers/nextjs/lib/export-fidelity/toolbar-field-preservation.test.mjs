import assert from "node:assert/strict";
import test from "node:test";

import { renderTemplateV2GeneralPresentationHtml } from "../template-v2-general-renderer.mjs";
import {
  projectTemplateV2ElementForRenderPlan,
  translateTemplateV2Vector,
} from "../template-v2-vector.ts";

const frame = {
  position: { x: 20, y: 30 },
  size: { width: 320, height: 160 },
};

function roundTripThroughExportBoundary(template) {
  return JSON.parse(JSON.stringify(template));
}

test("toolbar image, icon, and text fields survive persisted JSON and export rendering", () => {
  const template = {
    version: "v2-standard",
    mode: "template",
    slides: [{
      id: "toolbar-fidelity",
      ui: {
        components: [{
          id: "body",
          position: { x: 0, y: 0 },
          elements: [
            {
              type: "image",
              ...frame,
              data: "data:image/png;base64,AA==",
              fit: "cover",
              focus_x: 18,
              focus_y: 82,
              crop_scale: 0.6,
              flip_h: true,
              flip_v: true,
              border_radius: { tl: 7, tr: 11, br: 13, bl: 17 },
              opacity: 0.42,
              name: "cropped image",
            },
            {
              type: "image",
              position: { x: 380, y: 30 },
              size: { width: 80, height: 80 },
              data: "/static/icons/regular/star.svg",
              is_icon: true,
              color: "#12ab34",
              opacity: 0.9,
              name: "toolbar icon",
            },
            {
              type: "text",
              position: { x: 20, y: 220 },
              size: { width: 500, height: 120 },
              alignment: { horizontal: "center", vertical: "middle" },
              font: {
                family: "Inter",
                size: 31,
                color: "#123abc",
                bold: true,
                italic: true,
                underline: true,
                letter_spacing: 2.5,
                line_height: 1.4,
                opacity: 0.67,
              },
              runs: [{ text: "Toolbar text" }],
              name: "toolbar text",
            },
            {
              type: "text-list",
              position: { x: 20, y: 380 },
              size: { width: 500, height: 120 },
              marker: "number",
              font: {
                family: "Arial",
                size: 22,
                color: "#334455",
                letter_spacing: 1,
                line_height: 30,
                opacity: 0.75,
              },
              items: [[{ text: "First" }], [{ text: "Second" }]],
              name: "toolbar list",
            },
          ],
        }],
      },
    }],
  };

  const persisted = roundTripThroughExportBoundary(template);
  const [image, icon, textElement, textList] =
    persisted.slides[0].ui.components[0].elements;

  assert.deepEqual(
    {
      fit: image.fit,
      focus_x: image.focus_x,
      focus_y: image.focus_y,
      crop_scale: image.crop_scale,
      flip_h: image.flip_h,
      flip_v: image.flip_v,
      border_radius: image.border_radius,
      opacity: image.opacity,
    },
    {
      fit: "cover",
      focus_x: 18,
      focus_y: 82,
      crop_scale: 0.6,
      flip_h: true,
      flip_v: true,
      border_radius: { tl: 7, tr: 11, br: 13, bl: 17 },
      opacity: 0.42,
    }
  );
  assert.deepEqual(
    {
      is_icon: icon.is_icon,
      data: icon.data,
      color: icon.color,
      opacity: icon.opacity,
    },
    {
      is_icon: true,
      data: "/static/icons/regular/star.svg",
      color: "#12ab34",
      opacity: 0.9,
    }
  );
  assert.deepEqual(textElement.font, template.slides[0].ui.components[0].elements[2].font);
  assert.deepEqual(textElement.alignment, { horizontal: "center", vertical: "middle" });
  assert.equal(textList.marker, "number");
  assert.deepEqual(textList.font, template.slides[0].ui.components[0].elements[3].font);

  const html = renderTemplateV2GeneralPresentationHtml(persisted);
  assert.match(html, /data-image-crop-scale="0.6"/);
  assert.match(html, /object-position:18% 82%/);
  assert.match(html, /transform:scale\(0.6\)/);
  assert.match(html, /transform-origin:18% 82%/);
  assert.match(html, /transform:scaleX\(-1\) scaleY\(-1\)/);
  assert.match(html, /border-radius:7px 11px 13px 17px/);
  assert.match(html, /opacity:0.42/);
  assert.match(html, /data-template-v2-image-mask/);
  assert.match(html, /background:#12ab34/);
  assert.match(html, /font-family:&quot;Inter&quot;/);
  assert.match(html, /font-size:31px/);
  assert.match(html, /color:#123abc/);
  assert.match(html, /font-weight:700/);
  assert.match(html, /font-style:italic/);
  assert.match(html, /text-decoration:underline/);
  assert.match(html, /letter-spacing:2.5px/);
  assert.match(html, /line-height:1.4/);
  assert.match(html, /opacity:0.67/);
  assert.match(html, /text-align:center/);
  assert.match(html, /justify-content:center/);
  assert.match(html, /<ol data-template-v2-element="text-list"/);
  assert.match(html, /list-style-type:decimal/);
});

test("vector appearance fields remain exact while planning and export consume them", () => {
  const vector = {
    type: "vector",
    points: [
      { x: 40, y: 40, future_point: { pressure: 0.75 } },
      { x: 200, y: 40 },
      { x: 200, y: 160 },
      { x: 40, y: 160 },
    ],
    closed: true,
    corner_radii: [12.125, 8.5, 4.25, 2],
    curve: { type: "smooth", tension: 0.35, segments: 4 },
    fill: { color: "#123456", opacity: 0.35, future_fill: "kept" },
    stroke: {
      color: "#abcdef",
      opacity: 0.6,
      width: 3,
      dash: [7, 3],
      future_stroke: true,
    },
    shadow: {
      color: "#112233",
      opacity: 0.4,
      blur: 9.5,
      offset_x: 6.25,
      offset_y: -3.5,
      future_shadow: [1, 2],
    },
    opacity: 0.8,
    future_vector: { retained: true },
  };
  const authoredSnapshot = structuredClone(vector);
  const persisted = roundTripThroughExportBoundary({
    version: "v2-standard",
    mode: "template",
    slides: [{
      id: "vector-toolbar-fidelity",
      ui: {
        components: [{
          id: "body",
          position: { x: 0, y: 0 },
          elements: [vector],
        }],
      },
    }],
  });

  assert.deepEqual(
    persisted.slides[0].ui.components[0].elements[0],
    authoredSnapshot
  );

  const projected = projectTemplateV2ElementForRenderPlan(vector);
  assert.notStrictEqual(projected, vector);
  assert.deepEqual(projected.stroke.dash, []);
  assert.deepEqual(vector, authoredSnapshot);
  assert.equal(projected.stroke.future_stroke, true);
  assert.strictEqual(projected.fill, vector.fill);
  assert.strictEqual(projected.shadow, vector.shadow);

  const translated = translateTemplateV2Vector(vector, 5, -2);
  assert.deepEqual(translated.corner_radii, vector.corner_radii);
  assert.deepEqual(translated.fill, vector.fill);
  assert.deepEqual(translated.stroke, vector.stroke);
  assert.deepEqual(translated.shadow, vector.shadow);
  assert.deepEqual(translated.future_vector, vector.future_vector);
  assert.deepEqual(translated.points[0].future_point, { pressure: 0.75 });

  const html = renderTemplateV2GeneralPresentationHtml(persisted);
  assert.match(html, /fill="rgba\(18,52,86,0.35\)"/);
  assert.match(html, /stroke="rgba\(171,205,239,0.6\)"/);
  assert.match(html, /stroke-dasharray="7 3"/);
  assert.match(
    html,
    /filter:drop-shadow\(6.25px -3.5px 9.5px rgba\(17,34,51,0.4\)\)/
  );
  assert.match(html, /opacity="0.8"/);
  const polygonPoints = html.match(/<polygon points="([^"]+)"/)?.[1];
  assert.ok(polygonPoints);
  assert.ok(polygonPoints.split(" ").length > vector.points.length);
  assert.deepEqual(vector, authoredSnapshot);
});
