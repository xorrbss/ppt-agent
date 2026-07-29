import assert from "node:assert/strict";
import test from "node:test";

import { renderTemplateV2GeneralPresentationHtml } from "../template-v2-general-renderer.mjs";

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
