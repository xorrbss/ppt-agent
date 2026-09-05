/**
 * Plain JavaScript spliced into the authored-hybrid browser source. Chrome
 * executes it inside the observation IIFE, so it must stay plain ES5-style
 * source: no imports, no bundler, and nothing that survives only under a
 * module scope.
 *
 * This module owns CSS paint parsing - turning the two computed-style values
 * that describe a filled surface into the numbers the OOXML side needs:
 * `parseColor` normalizes any `rgb()`/`rgba()` string into an uppercase
 * six-digit sRGB hex plus a separate 0-1 alpha (DrawingML carries opacity as
 * its own `<a:alpha>`, never baked into the colour), and `parseLinearGradient`
 * resolves a `linear-gradient()` into an angle in degrees plus fully
 * positioned stops. `splitCssArguments` and `cssGradientAngle` exist only to
 * serve the latter: the first splits on top-level commas so an `rgba(...)`
 * stop is not torn apart, the second maps CSS keyword directions onto the same
 * clockwise-from-top degrees that DrawingML's `<a:lin ang>` uses.
 *
 * Gradients are resolved here rather than left to PowerPoint because CSS
 * allows stops to omit their position and to be expressed in px against the
 * gradient axis; both are interpolated away so the emitted stop list is
 * already monotonic and in the 0-1 domain.
 *
 * It relies on the numeric primitives (`round`, `clamp`, `number`) hoisted
 * beside it in the same IIFE, so it must stay spliced into that scope rather
 * than evaluated on its own.
 */
export const AUTHORED_HYBRID_CSS_PAINT_BROWSER_SOURCE = String.raw`
  function parseColor(value) {
    if (!value || value === "transparent") {
      return { hex: "000000", alpha: 0 };
    }
    var match = value.match(
      /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/i
    );
    if (!match) return null;
    var red = clamp(Math.round(number(match[1], 0)), 0, 255);
    var green = clamp(Math.round(number(match[2], 0)), 0, 255);
    var blue = clamp(Math.round(number(match[3], 0)), 0, 255);
    var alpha = 1;
    if (match[4]) {
      alpha = match[4].endsWith("%")
        ? number(match[4], 100) / 100
        : number(match[4], 1);
    }
    var hex = [red, green, blue]
      .map(function (channel) {
        return channel.toString(16).padStart(2, "0").toUpperCase();
      })
      .join("");
    return { hex: hex, alpha: round(clamp(alpha, 0, 1)) };
  }

  function splitCssArguments(value) {
    var parts = [];
    var depth = 0;
    var start = 0;
    for (var index = 0; index < value.length; index += 1) {
      var character = value[index];
      if (character === "(") depth += 1;
      if (character === ")") depth = Math.max(0, depth - 1);
      if (character === "," && depth === 0) {
        parts.push(value.slice(start, index).trim());
        start = index + 1;
      }
    }
    parts.push(value.slice(start).trim());
    return parts.filter(Boolean);
  }

  function cssGradientAngle(value) {
    var normalized = String(value || "").trim().toLowerCase();
    var degrees = normalized.match(/^(-?(?:\d+(?:\.\d+)?|\.\d+))deg$/);
    if (degrees) return number(degrees[1], 180);
    var directions = {
      "to top": 0,
      "to top right": 45,
      "to right top": 45,
      "to right": 90,
      "to bottom right": 135,
      "to right bottom": 135,
      "to bottom": 180,
      "to bottom left": 225,
      "to left bottom": 225,
      "to left": 270,
      "to top left": 315,
      "to left top": 315,
    };
    return Object.prototype.hasOwnProperty.call(directions, normalized)
      ? directions[normalized]
      : null;
  }

  function parseLinearGradient(value, bounds) {
    var match = String(value || "").trim().match(/^linear-gradient\((.*)\)$/i);
    if (!match) return null;
    var parts = splitCssArguments(match[1]);
    if (parts.length < 2) return null;
    var angle = cssGradientAngle(parts[0]);
    if (angle !== null) parts.shift();
    else angle = 180;
    var radians = angle * Math.PI / 180;
    var axisLength = Math.max(
      0.01,
      Math.abs(Math.sin(radians)) * bounds.width +
        Math.abs(Math.cos(radians)) * bounds.height
    );
    var stops = parts.map(function (part) {
      var stop = part.match(/^(rgba?\([^)]*\)|#[0-9a-f]{3,8})(?:\s+(.+))?$/i);
      if (!stop) return null;
      var color = parseColor(stop[1]);
      if (!color) return null;
      var position = null;
      if (stop[2]) {
        var positionValue = stop[2].trim().split(/\s+/)[0];
        if (/%$/.test(positionValue)) {
          position = number(positionValue, Number.NaN) / 100;
        } else if (/px$/.test(positionValue)) {
          position = number(positionValue, Number.NaN) / axisLength;
        } else if (/^0(?:\.0+)?$/.test(positionValue)) {
          position = 0;
        }
        if (position !== null && !Number.isFinite(position)) return null;
      }
      return { color: color, position: position };
    });
    if (stops.length < 2 || stops.some(function (stop) { return !stop; })) return null;
    if (stops[0].position === null) stops[0].position = 0;
    if (stops[stops.length - 1].position === null) stops[stops.length - 1].position = 1;
    var cursor = 0;
    while (cursor < stops.length) {
      if (stops[cursor].position !== null) {
        cursor += 1;
        continue;
      }
      var runStart = cursor;
      while (cursor < stops.length && stops[cursor].position === null) cursor += 1;
      var previous = stops[runStart - 1].position;
      var next = stops[cursor].position;
      var count = cursor - runStart + 1;
      for (var offset = 0; offset < cursor - runStart; offset += 1) {
        stops[runStart + offset].position = previous + (next - previous) * (offset + 1) / count;
      }
    }
    var last = 0;
    stops.forEach(function (stop) {
      stop.position = round(clamp(Math.max(last, stop.position), 0, 1));
      last = stop.position;
    });
    return { angleDeg: round(angle), stops: stops };
  }
`;
