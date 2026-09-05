/**
 * Plain JavaScript spliced into the authored-hybrid browser source. Chrome
 * executes it inside the observation IIFE, so it must stay self-contained:
 * no imports and no helpers from the surrounding scope. Node tests evaluate
 * the same text, so the browser classifier and the OOXML assembler agree on
 * which CSS `box-shadow` layers become one native DrawingML outer shadow.
 *
 * Mapping rule (shared with `shadow-effect.ts`):
 * - every layer must parse (rgb/rgba colour, px lengths); otherwise nothing maps;
 * - `inset` anywhere disables mapping (DrawingML `innerShdw` is not emitted);
 * - at most two layers are considered, because `<a:effectLst>` carries a
 *   single `<a:outerShdw>`; the visually strongest layer is emitted natively
 *   and the remaining layer stays painted on the residual backplate.
 */
export const AUTHORED_HYBRID_SHADOW_BROWSER_SOURCE = String.raw`
  var OUTER_SHADOW_MAX_LAYERS = 2;

  function shadowRound(value) {
    return Math.round(value * 1000) / 1000;
  }

  function shadowColor(text) {
    var match = text.match(
      /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/i
    );
    if (!match) return null;
    var alpha = 1;
    if (match[4]) {
      alpha = match[4].slice(-1) === "%"
        ? Number(match[4].slice(0, -1)) / 100
        : Number(match[4]);
    }
    if (!Number.isFinite(alpha)) return null;
    var hex = [match[1], match[2], match[3]].map(function (channel) {
      var value = Math.max(0, Math.min(255, Math.round(Number(channel))));
      return value.toString(16).padStart(2, "0").toUpperCase();
    }).join("");
    return { hex: hex, alpha: shadowRound(Math.max(0, Math.min(1, alpha))) };
  }

  function splitShadowLayers(value) {
    var parts = [];
    var depth = 0;
    var start = 0;
    for (var index = 0; index < value.length; index += 1) {
      var character = value[index];
      if (character === "(") depth += 1;
      else if (character === ")") depth = Math.max(0, depth - 1);
      else if (character === "," && depth === 0) {
        parts.push(value.slice(start, index).trim());
        start = index + 1;
      }
    }
    parts.push(value.slice(start).trim());
    return parts.filter(Boolean);
  }

  /** Parse a computed box-shadow list. Null when any layer is not understood. */
  function parseBoxShadowLayers(boxShadow) {
    if (!boxShadow || boxShadow === "none") return [];
    var layers = [];
    var parts = splitShadowLayers(boxShadow);
    for (var index = 0; index < parts.length; index += 1) {
      var text = parts[index];
      var colorMatch = text.match(/rgba?\([^)]*\)/i);
      var color = colorMatch ? shadowColor(colorMatch[0]) : null;
      var rest = colorMatch ? text.replace(colorMatch[0], " ") : text;
      var inset = /\binset\b/i.test(rest);
      var tokens = rest.replace(/\binset\b/i, " ").trim().split(/\s+/).filter(Boolean);
      if (!color || tokens.length < 2 || tokens.length > 4) return null;
      var lengths = [];
      for (var position = 0; position < tokens.length; position += 1) {
        var length = tokens[position].match(/^(-?(?:\d+(?:\.\d+)?|\.\d+))px$/);
        if (!length) return null;
        lengths.push(Number(length[1]));
      }
      layers.push({
        inset: inset,
        offsetXPx: shadowRound(lengths[0]),
        offsetYPx: shadowRound(lengths[1]),
        blurPx: shadowRound(Math.max(0, lengths[2] || 0)),
        spreadPx: shadowRound(lengths[3] || 0),
        color: color,
        css: text,
      });
    }
    return layers;
  }

  /** The strongest simple outer layer, or null when the list cannot map natively. */
  function selectOuterShadow(layers) {
    if (!layers || layers.length === 0 || layers.length > OUTER_SHADOW_MAX_LAYERS) return null;
    var best = null;
    for (var index = 0; index < layers.length; index += 1) {
      var layer = layers[index];
      if (layer.inset) return null;
      if (layer.color.alpha <= 0) continue;
      var weight = layer.color.alpha * (
        layer.blurPx + Math.max(0, layer.spreadPx) +
        Math.hypot(layer.offsetXPx, layer.offsetYPx) + 1
      );
      if (best && weight <= best.weight) continue;
      best = {
        index: index,
        weight: weight,
        shadow: {
          offsetXPx: layer.offsetXPx,
          offsetYPx: layer.offsetYPx,
          blurPx: layer.blurPx,
          spreadPx: layer.spreadPx,
          color: layer.color,
        },
      };
    }
    return best ? { index: best.index, shadow: best.shadow } : null;
  }

  function residualBoxShadowCss(layers, selectedIndex) {
    var residual = (layers || []).filter(function (_layer, index) {
      return index !== selectedIndex;
    }).map(function (layer) { return layer.css; });
    return residual.length ? residual.join(", ") : "none";
  }

  /** CSS left for the backplate once the mapped layer is native; unchanged when nothing maps. */
  function mappedResidualBoxShadow(boxShadow) {
    var layers = parseBoxShadowLayers(boxShadow);
    var selected = selectOuterShadow(layers);
    return selected ? residualBoxShadowCss(layers, selected.index) : (boxShadow || "none");
  }

  function boxShadowUnmapped(boxShadow) {
    return Boolean(boxShadow) && boxShadow !== "none" &&
      !selectOuterShadow(parseBoxShadowLayers(boxShadow));
  }

  /** Shape-payload shadow fields for one computed box-shadow value. */
  function boxShadowPlan(boxShadow) {
    var painted = Boolean(boxShadow) && boxShadow !== "none";
    var layers = parseBoxShadowLayers(boxShadow);
    var selected = selectOuterShadow(layers);
    var residual = layers ? layers.filter(function (_layer, index) {
      return !selected || index !== selected.index;
    }) : null;
    return {
      outerShadow: selected ? selected.shadow : undefined,
      backplateShadowLayers: residual ? residual.length : (painted ? 1 : 0),
      shadowLayers: (residual || []).filter(function (layer) {
        return !layer.inset && layer.color.alpha > 0 && layer.blurPx <= 0.01;
      }).map(function (layer) {
        return {
          offsetXPx: layer.offsetXPx,
          offsetYPx: layer.offsetYPx,
          spreadPx: layer.spreadPx,
          color: layer.color,
        };
      }),
    };
  }
`;
