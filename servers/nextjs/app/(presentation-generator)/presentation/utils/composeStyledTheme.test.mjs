// node --experimental-strip-types composeStyledTheme.test.mjs
import assert from "node:assert";
import { composeStyledTheme } from "./composeStyledTheme.ts";

const style = {
  data: {
    colors: { primary: "#000", background: "#fff" }, // should be REPLACED
    fonts: { textFont: { name: "Inter", url: "u" }, headingFont: { name: "Space Grotesk", url: "u2" } },
    density: "compact",
    typography: { scale: 0.98, headingWeight: 700 },
    shape: { radiusScale: 0, borderWidth: "1px" },
    elevation: { flat: true },
  },
};
const brand = { primary: "#1f6feb", background: "#0d1117", card: "#161b22", graph_0: "#79c0ff" };

const data = composeStyledTheme(style, brand);
assert.deepStrictEqual(data.colors, brand, "colours replaced with brand set");
assert.deepStrictEqual(data.fonts, style.data.fonts, "fonts taken from style");
assert.strictEqual(data.density, "compact", "density carried");
assert.deepStrictEqual(data.typography, style.data.typography, "typography carried");
assert.deepStrictEqual(data.shape, style.data.shape, "shape carried");
assert.deepStrictEqual(data.elevation, style.data.elevation, "elevation carried");
assert.ok(!("motif" in data), "absent style key not added");

// v1-style preset (colours + fonts only) → only colors+fonts in result
const v1 = { data: { colors: {}, fonts: { textFont: { name: "Lato", url: "u" } } } };
const d2 = composeStyledTheme(v1, brand);
assert.deepStrictEqual(Object.keys(d2).sort(), ["colors", "fonts"], "v1 style yields colors+fonts only");

// null style → no crash, colors + undefined fonts
const d3 = composeStyledTheme(null, brand);
assert.deepStrictEqual(d3.colors, brand);

console.log("composeStyledTheme: all assertions passed");
