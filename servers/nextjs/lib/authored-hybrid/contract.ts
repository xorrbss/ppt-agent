import { createHash } from "node:crypto";

import {
  AUTHORED_CSS_DPI,
  AUTHORED_HYBRID_FALLBACK_REASONS,
  AUTHORED_HYBRID_SCHEMA_VERSION,
  AUTHORED_SLIDE_HEIGHT_PX,
  AUTHORED_SLIDE_WIDTH_PX,
  type AuthoredHybridElement,
  type AuthoredHybridElementBase,
  type AuthoredHybridShapePayload,
  type AuthoredHybridSlideV1,
  type AuthoredHybridTextPayload,
} from "./schema.ts";
import { classifyAuthoredHybridCandidate } from "./classifier.ts";
import type {
  BrowserAuthoredHybridObservation,
  BrowserElementObservation,
} from "./observation.ts";
import {
  AUTHORED_SLIDE_HEIGHT_IN,
  AUTHORED_SLIDE_WIDTH_IN,
  rectPxToBounds,
} from "./units.ts";

const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const HEX_COLOR_PATTERN = /^[A-F0-9]{6}$/;
const FALLBACK_REASON_SET = new Set<string>(AUTHORED_HYBRID_FALLBACK_REASONS);

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Invalid authored hybrid contract: ${message}`);
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function hasTextPayload(
  element: AuthoredHybridElement
): element is AuthoredHybridElement & { text: AuthoredHybridTextPayload } {
  return "text" in element && element.text !== undefined;
}

function hasShapePayload(
  element: AuthoredHybridElement
): element is AuthoredHybridElement & { shape: AuthoredHybridShapePayload } {
  return "shape" in element && element.shape !== undefined;
}

const UNSAFE_EDITABLE_SHAPE_REASONS = new Set([
  "extraction-error",
  "invalid-bounds",
  "outside-slide",
  "clip-path",
  "mask",
  "filter",
  "backdrop-filter",
  "mix-blend-mode",
  "complex-transform",
  "transformed-ancestor",
  "animated",
  "unsupported-background",
  "unsupported-opacity",
]);

function isEditableRasterShapePayload(
  element: AuthoredHybridElement
): element is AuthoredHybridElement & { shape: AuthoredHybridShapePayload } {
  return (
    element.classification.mode === "raster" &&
    element.classification.candidateKind === "shape" &&
    hasShapePayload(element) &&
    !element.classification.reasons.some((reason) =>
      UNSAFE_EDITABLE_SHAPE_REASONS.has(reason)
    ) &&
    Boolean(
      element.shape.fill ||
      element.shape.gradient ||
      element.shape.stroke ||
      element.shape.borderLines?.length ||
      element.shape.shadowLayers?.length
    )
  );
}

function oneOf(value: unknown, allowed: readonly string[], label: string): void {
  invariant(
    typeof value === "string" && allowed.includes(value),
    `${label} is invalid`
  );
}

function assertStringArray(value: unknown, label: string): asserts value is string[] {
  invariant(
    Array.isArray(value) && value.every((item) => typeof item === "string"),
    `${label} must be an array of strings`
  );
}

function assertColor(value: unknown, label: string): void {
  invariant(typeof value === "object" && value !== null, `${label} is missing`);
  const color = value as Record<string, unknown>;
  invariant(
    typeof color.hex === "string" && HEX_COLOR_PATTERN.test(color.hex),
    `${label}.hex must be six-digit uppercase sRGB`
  );
  invariant(
    finite(color.alpha) && color.alpha >= 0 && color.alpha <= 1,
    `${label}.alpha must be between 0 and 1`
  );
}

function assertTextStyle(value: unknown, label: string): void {
  invariant(typeof value === "object" && value !== null, `${label} is missing`);
  const style = value as Record<string, unknown>;
  invariant(typeof style.fontFamily === "string" && style.fontFamily.length > 0, `${label}.fontFamily is invalid`);
  assertStringArray(style.fontFamilies, `${label}.fontFamilies`);
  assertStringArray(style.cjkFallbackFamilies, `${label}.cjkFallbackFamilies`);
  for (const key of ["fontSizePt", "fontWeight", "letterSpacingPt"] as const) {
    invariant(finite(style[key]), `${label}.${key} must be finite`);
  }
  invariant((style.fontSizePt as number) >= 0, `${label}.fontSizePt is negative`);
  for (const key of ["bold", "italic", "underline", "strike"] as const) {
    invariant(typeof style[key] === "boolean", `${label}.${key} must be boolean`);
  }
  assertColor(style.color, `${label}.color`);
  invariant(typeof style.lineHeight === "object" && style.lineHeight !== null, `${label}.lineHeight is missing`);
  const lineHeight = style.lineHeight as Record<string, unknown>;
  invariant(finite(lineHeight.points) && lineHeight.points >= 0, `${label}.lineHeight.points is invalid`);
  invariant(finite(lineHeight.multiple) && lineHeight.multiple >= 0, `${label}.lineHeight.multiple is invalid`);
  oneOf(lineHeight.source, ["computed", "normal"], `${label}.lineHeight.source`);
  oneOf(style.horizontalAlignment, ["left", "center", "right", "justify"], `${label}.horizontalAlignment`);
  oneOf(style.verticalAlignment, ["top", "middle", "bottom"], `${label}.verticalAlignment`);
  oneOf(style.direction, ["ltr", "rtl"], `${label}.direction`);
  if (style.wrapMode !== undefined) {
    oneOf(style.wrapMode, ["wrap", "no-wrap"], `${label}.wrapMode`);
  }
}

function assertShapePayload(value: unknown, label: string): void {
  invariant(typeof value === "object" && value !== null, `${label} is missing`);
  const shape = value as Record<string, unknown>;
  oneOf(shape.shape, ["rectangle", "round-rectangle", "ellipse", "line", "freeform"], `${label}.shape`);
  if (shape.fill !== null) assertColor(shape.fill, `${label}.fill`);
  if (shape.gradient !== undefined) {
    invariant(typeof shape.gradient === "object" && shape.gradient !== null, `${label}.gradient is invalid`);
    const gradient = shape.gradient as Record<string, unknown>;
    invariant(finite(gradient.angleDeg), `${label}.gradient.angleDeg must be finite`);
    invariant(Array.isArray(gradient.stops) && gradient.stops.length >= 2, `${label}.gradient.stops must contain at least two stops`);
    let previousPosition = -1;
    gradient.stops.forEach((stop, index) => {
      invariant(typeof stop === "object" && stop !== null, `${label}.gradient.stops[${index}] is invalid`);
      const gradientStop = stop as Record<string, unknown>;
      assertColor(gradientStop.color, `${label}.gradient.stops[${index}].color`);
      invariant(
        finite(gradientStop.position) && gradientStop.position >= 0 && gradientStop.position <= 1,
        `${label}.gradient.stops[${index}].position must be between 0 and 1`
      );
      invariant(gradientStop.position >= previousPosition, `${label}.gradient.stops must be ordered`);
      previousPosition = gradientStop.position;
    });
  }
  if (shape.stroke !== null) assertColor(shape.stroke, `${label}.stroke`);
  invariant(finite(shape.strokeWidthPt) && shape.strokeWidthPt >= 0, `${label}.strokeWidthPt is invalid`);
  if (shape.dash !== undefined) {
    oneOf(shape.dash, ["dash", "dot"], `${label}.dash`);
  }
  if (shape.lineCap !== undefined) {
    oneOf(shape.lineCap, ["round"], `${label}.lineCap`);
  }
  if (shape.lineJoin !== undefined) {
    oneOf(shape.lineJoin, ["round"], `${label}.lineJoin`);
  }
  invariant(finite(shape.radiusPt) && shape.radiusPt >= 0, `${label}.radiusPt is invalid`);
  if (shape.borderLines !== undefined) {
    invariant(Array.isArray(shape.borderLines), `${label}.borderLines must be an array`);
    shape.borderLines.forEach((line, index) => {
      invariant(typeof line === "object" && line !== null, `${label}.borderLines[${index}] is invalid`);
      const borderLine = line as Record<string, unknown>;
      oneOf(borderLine.side, ["top", "right", "bottom", "left"], `${label}.borderLines[${index}].side`);
      assertColor(borderLine.color, `${label}.borderLines[${index}].color`);
      invariant(finite(borderLine.widthPt) && borderLine.widthPt > 0, `${label}.borderLines[${index}].widthPt is invalid`);
      if (borderLine.dash !== undefined) {
        oneOf(borderLine.dash, ["dash", "dot"], `${label}.borderLines[${index}].dash`);
      }
    });
  }
  if (shape.shadowLayers !== undefined) {
    invariant(Array.isArray(shape.shadowLayers), `${label}.shadowLayers must be an array`);
    shape.shadowLayers.forEach((layer, index) => {
      invariant(typeof layer === "object" && layer !== null, `${label}.shadowLayers[${index}] is invalid`);
      const shadowLayer = layer as Record<string, unknown>;
      invariant(finite(shadowLayer.offsetXPx), `${label}.shadowLayers[${index}].offsetXPx is invalid`);
      invariant(finite(shadowLayer.offsetYPx), `${label}.shadowLayers[${index}].offsetYPx is invalid`);
      invariant(finite(shadowLayer.spreadPx), `${label}.shadowLayers[${index}].spreadPx is invalid`);
      assertColor(shadowLayer.color, `${label}.shadowLayers[${index}].color`);
    });
  }
  if (shape.endArrow !== undefined) {
    oneOf(shape.endArrow, ["triangle"], `${label}.endArrow`);
  }
  if (shape.points !== undefined) {
    invariant(Array.isArray(shape.points), `${label}.points must be an array`);
    invariant(shape.points.length >= 2 && shape.points.length <= 256, `${label}.points length is invalid`);
    shape.points.forEach((point, index) => {
      invariant(typeof point === "object" && point !== null, `${label}.points[${index}] is invalid`);
      const vertex = point as Record<string, unknown>;
      invariant(
        finite(vertex.x) && vertex.x >= 0 && vertex.x <= 1,
        `${label}.points[${index}].x is invalid`
      );
      invariant(
        finite(vertex.y) && vertex.y >= 0 && vertex.y <= 1,
        `${label}.points[${index}].y is invalid`
      );
    });
  }
  if (shape.shape === "freeform") {
    invariant(Array.isArray(shape.points) && shape.points.length >= 2, `${label}.points are required for a freeform`);
  }
  if (shape.closed !== undefined) {
    invariant(typeof shape.closed === "boolean", `${label}.closed is invalid`);
  }
  if (shape.preserveContents !== undefined) {
    invariant(typeof shape.preserveContents === "boolean", `${label}.preserveContents is invalid`);
  }
}

function convertTextPayload(
  observation: NonNullable<BrowserElementObservation["text"]>
): AuthoredHybridTextPayload {
  const { containerShape, ...text } = observation;
  return {
    ...text,
    role: observation.role === "unsupported" ? "body" : observation.role,
    runs: observation.runs.map((run) => ({
      text: run.text,
      bounds: rectPxToBounds(run.boundsPx),
      fragments: run.fragmentRectsPx.map(rectPxToBounds),
      style: run.style,
    })),
    ...(containerShape
      ? { containerShape: {
          bounds: rectPxToBounds(containerShape.boundsPx),
          shape: containerShape.shape,
        } }
      : {}),
  };
}

function convertElement(
  observation: BrowserElementObservation,
  zOrder: number
): AuthoredHybridElement {
  const classification = classifyAuthoredHybridCandidate({
    candidateKind: observation.candidateKind,
    textRole: observation.text?.role,
    hasPayload:
      observation.candidateKind === "complex" ||
      (observation.candidateKind === "text" && Boolean(observation.text)) ||
      (observation.candidateKind === "image" && Boolean(observation.image)) ||
      (observation.candidateKind === "shape" && Boolean(observation.shape)),
    fallbackReasons: observation.fallbackReasons,
  });
  const base: AuthoredHybridElementBase = {
    id: observation.id,
    domPath: observation.domPath,
    tagName: observation.tagName,
    sourceIndex: observation.sourceIndex,
    zOrder,
    cssZIndex: observation.cssZIndex,
    bounds: rectPxToBounds(observation.boundsPx),
    rotationDeg: observation.rotationDeg,
    opacity: observation.opacity,
  };

  if (classification.mode === "raster") {
    if (observation.candidateKind === "text" && observation.text) {
      return {
        ...base,
        classification,
        text: convertTextPayload(observation.text),
      };
    }
    if (observation.candidateKind === "shape" && observation.shape) {
      return {
        ...base,
        classification,
        shape: observation.shape,
      };
    }
    return { ...base, classification };
  }
  if (classification.kind === "text" && observation.text) {
    if (observation.text.role === "unsupported") {
      throw new Error(
        `Authored hybrid classifier promoted unsupported text role for ${observation.id}.`
      );
    }
    return {
      ...base,
      classification: { mode: "native", kind: "text", confidence: "safe" },
      text: convertTextPayload(observation.text),
    };
  }
  if (classification.kind === "image" && observation.image) {
    return {
      ...base,
      classification: { mode: "native", kind: "image", confidence: "safe" },
      image: observation.image,
    };
  }
  if (classification.kind === "shape" && observation.shape) {
    return {
      ...base,
      classification: { mode: "native", kind: "shape", confidence: "safe" },
      shape: observation.shape,
    };
  }

  // Classification is fail-closed, so this path indicates a programming
  // contract violation rather than a slide-specific unsupported element.
  throw new Error(
    `Authored hybrid classifier promoted ${observation.id} without a matching payload.`
  );
}

export function authoredHtmlSha256(html: string): string {
  return createHash("sha256").update(html, "utf8").digest("hex");
}

export function buildAuthoredHybridSlide(
  html: string,
  browserObservation: BrowserAuthoredHybridObservation,
  baseUrl: string | undefined
): AuthoredHybridSlideV1 {
  invariant(
    browserObservation.viewport.widthPx === AUTHORED_SLIDE_WIDTH_PX &&
      browserObservation.viewport.heightPx === AUTHORED_SLIDE_HEIGHT_PX,
    `expected ${AUTHORED_SLIDE_WIDTH_PX}x${AUTHORED_SLIDE_HEIGHT_PX} viewport, received ${browserObservation.viewport.widthPx}x${browserObservation.viewport.heightPx}`
  );
  invariant(
    browserObservation.viewport.devicePixelRatio === 1,
    `expected devicePixelRatio 1, received ${browserObservation.viewport.devicePixelRatio}`
  );
  invariant(
    browserObservation.appliedPromotedElementIds.length === 0,
    "analysis capture must not suppress promoted elements"
  );
  invariant(
    browserObservation.rejectedPromotedElementIds.length === 0,
    "analysis capture must not reject promoted elements"
  );

  const ordered = [...browserObservation.elements].sort(
    (left, right) =>
      left.sourceIndex - right.sourceIndex ||
      left.id.localeCompare(right.id)
  );
  const elements = ordered.map(convertElement);
  const slide: AuthoredHybridSlideV1 = {
    schemaVersion: AUTHORED_HYBRID_SCHEMA_VERSION,
    source: {
      kind: "authored-html",
      htmlSha256: authoredHtmlSha256(html),
      baseUrl: baseUrl ?? null,
      viewport: {
        widthPx: AUTHORED_SLIDE_WIDTH_PX,
        heightPx: AUTHORED_SLIDE_HEIGHT_PX,
        cssDpi: AUTHORED_CSS_DPI,
        widthIn: AUTHORED_SLIDE_WIDTH_IN,
        heightIn: AUTHORED_SLIDE_HEIGHT_IN,
      },
    },
    elements,
    backplate: {
      strategy: "hide-native-leaves",
      mediaType: "image/png",
      widthPx: AUTHORED_SLIDE_WIDTH_PX,
      heightPx: AUTHORED_SLIDE_HEIGHT_PX,
      transparentBackground: true,
      eligibleElementIds: elements
        .filter(
          (element) =>
            element.classification.mode === "native" ||
            (element.classification.mode === "raster" &&
              ((element.classification.candidateKind === "text" && hasTextPayload(element)) ||
                (element.classification.candidateKind === "shape" && isEditableRasterShapePayload(element))))
        )
        .map((element) => element.id),
      rasterElementIds: elements
        .filter((element) => element.classification.mode === "raster")
        .map((element) => element.id),
    },
    warnings: [...new Set(browserObservation.warnings)].sort(),
  };
  assertAuthoredHybridSlide(slide);
  return slide;
}

function assertBounds(value: unknown, label: string): void {
  invariant(typeof value === "object" && value !== null, `${label} is missing`);
  const bounds = value as Record<string, unknown>;
  for (const unit of ["px", "inches"] as const) {
    const rect = bounds[unit];
    invariant(typeof rect === "object" && rect !== null, `${label}.${unit} is missing`);
    const values = rect as Record<string, unknown>;
    for (const key of ["x", "y", "width", "height"] as const) {
      invariant(finite(values[key]), `${label}.${unit}.${key} must be finite`);
    }
    invariant((values.width as number) >= 0, `${label}.${unit}.width is negative`);
    invariant((values.height as number) >= 0, `${label}.${unit}.height is negative`);
  }
  const px = bounds.px as Record<string, number>;
  const inches = bounds.inches as Record<string, number>;
  for (const key of ["x", "y", "width", "height"] as const) {
    const expected = Math.round((px[key] / AUTHORED_CSS_DPI) * 1_000_000) / 1_000_000;
    invariant(
      Math.abs(inches[key] - expected) <= 0.000001,
      `${label}.${key} violates px/96 inch conversion`
    );
  }
}

/** Runtime guard for persisted/cross-process H2 hand-off data. */
export function assertAuthoredHybridSlide(
  value: unknown
): asserts value is AuthoredHybridSlideV1 {
  invariant(typeof value === "object" && value !== null, "root must be an object");
  const slide = value as Partial<AuthoredHybridSlideV1>;
  invariant(
    slide.schemaVersion === AUTHORED_HYBRID_SCHEMA_VERSION,
    `unsupported schemaVersion ${String(slide.schemaVersion)}`
  );
  invariant(slide.source?.kind === "authored-html", "source.kind must be authored-html");
  invariant(
    typeof slide.source.htmlSha256 === "string" &&
      SHA256_PATTERN.test(slide.source.htmlSha256),
    "source.htmlSha256 must be a lowercase SHA-256 digest"
  );
  invariant(
    slide.source.baseUrl === null || typeof slide.source.baseUrl === "string",
    "source.baseUrl must be a string or null"
  );
  invariant(
    slide.source.viewport.widthPx === AUTHORED_SLIDE_WIDTH_PX &&
      slide.source.viewport.heightPx === AUTHORED_SLIDE_HEIGHT_PX &&
      slide.source.viewport.cssDpi === AUTHORED_CSS_DPI &&
      slide.source.viewport.widthIn === AUTHORED_SLIDE_WIDTH_IN &&
      slide.source.viewport.heightIn === AUTHORED_SLIDE_HEIGHT_IN,
    "source.viewport must use the fixed 1280x720 @ 96 CSS dpi contract"
  );
  invariant(Array.isArray(slide.elements), "elements must be an array");
  const ids = new Set<string>();
  const eligibleIds: string[] = [];
  const rasterIds: string[] = [];
  slide.elements.forEach((element, index) => {
    invariant(typeof element.id === "string" && element.id.length > 0, `elements[${index}].id is invalid`);
    invariant(!ids.has(element.id), `duplicate element id ${element.id}`);
    ids.add(element.id);
    invariant(typeof element.domPath === "string" && element.domPath.length > 0, `element ${element.id} has invalid domPath`);
    invariant(typeof element.tagName === "string" && element.tagName.length > 0, `element ${element.id} has invalid tagName`);
    invariant(element.zOrder === index, `element ${element.id} has non-contiguous zOrder`);
    invariant(Number.isInteger(element.sourceIndex) && element.sourceIndex >= 0, `element ${element.id} has invalid sourceIndex`);
    invariant(element.cssZIndex === null || Number.isInteger(element.cssZIndex), `element ${element.id} has invalid cssZIndex`);
    invariant(finite(element.rotationDeg), `element ${element.id} has invalid rotation`);
    invariant(finite(element.opacity) && element.opacity >= 0 && element.opacity <= 1, `element ${element.id} has invalid opacity`);
    assertBounds(element.bounds, `element ${element.id}.bounds`);

    if (element.classification.mode === "native") {
      eligibleIds.push(element.id);
      oneOf(element.classification.kind, ["text", "image", "shape"], `element ${element.id} native kind`);
      invariant(element.classification.confidence === "safe", `element ${element.id} native confidence is invalid`);
      invariant(
        (element.classification.kind === "text" && hasTextPayload(element)) ||
          (element.classification.kind === "image" && "image" in element) ||
          (element.classification.kind === "shape" && "shape" in element),
        `element ${element.id} native payload does not match its kind`
      );
      if (element.classification.kind === "text" && hasTextPayload(element)) {
        oneOf(element.text.role, ["title", "body", "numeric", "caption"], `element ${element.id} text.role`);
        invariant(typeof element.text.plainText === "string", `element ${element.id} text.plainText is invalid`);
        assertStringArray(element.text.paragraphs, `element ${element.id} text.paragraphs`);
        assertTextStyle(element.text.style, `element ${element.id} text.style`);
        invariant(Array.isArray(element.text.runs), `element ${element.id} text.runs must be an array`);
        element.text.runs.forEach((run, runIndex) => {
          invariant(typeof run.text === "string", `element ${element.id} run ${runIndex} text is invalid`);
          assertBounds(run.bounds, `element ${element.id} run ${runIndex}.bounds`);
          invariant(Array.isArray(run.fragments), `element ${element.id} run ${runIndex}.fragments must be an array`);
          run.fragments.forEach((fragment, fragmentIndex) =>
            assertBounds(fragment, `element ${element.id} run ${runIndex}.fragments[${fragmentIndex}]`)
          );
          assertTextStyle(run.style, `element ${element.id} run ${runIndex}.style`);
        });
        if (element.text.containerShape) {
          assertBounds(element.text.containerShape.bounds, `element ${element.id} text.containerShape.bounds`);
          assertShapePayload(element.text.containerShape.shape, `element ${element.id} text.containerShape.shape`);
        }
      } else if (element.classification.kind === "image" && "image" in element) {
        invariant(typeof element.image.src === "string", `element ${element.id} image.src is invalid`);
        invariant(typeof element.image.alt === "string", `element ${element.id} image.alt is invalid`);
        invariant(Number.isInteger(element.image.naturalWidth) && element.image.naturalWidth >= 0, `element ${element.id} image.naturalWidth is invalid`);
        invariant(Number.isInteger(element.image.naturalHeight) && element.image.naturalHeight >= 0, `element ${element.id} image.naturalHeight is invalid`);
        oneOf(element.image.objectFit, ["contain", "cover", "fill", "none", "scale-down"], `element ${element.id} image.objectFit`);
        invariant(typeof element.image.objectPosition === "string", `element ${element.id} image.objectPosition is invalid`);
        const crop = element.image.crop;
        invariant(typeof crop === "object" && crop !== null, `element ${element.id} image.crop is missing`);
        for (const key of ["left", "top", "right", "bottom"] as const) {
          invariant(finite(crop[key]) && crop[key] >= 0 && crop[key] <= 1, `element ${element.id} image.crop.${key} is invalid`);
        }
        invariant(crop.left + crop.right < 1 && crop.top + crop.bottom < 1, `element ${element.id} image.crop removes the entire image`);
      } else if (element.classification.kind === "shape" && "shape" in element) {
        assertShapePayload(element.shape, `element ${element.id} shape`);
      }
    } else {
      rasterIds.push(element.id);
      oneOf(element.classification.candidateKind, ["text", "image", "shape", "complex"], `element ${element.id} raster candidateKind`);
      invariant(
        Array.isArray(element.classification.reasons) &&
          element.classification.reasons.length > 0,
        `element ${element.id} raster fallback needs at least one reason`
      );
      invariant(
        new Set(element.classification.reasons).size === element.classification.reasons.length &&
          element.classification.reasons.every((reason) => FALLBACK_REASON_SET.has(reason)),
        `element ${element.id} raster fallback reasons are invalid`
      );
      if (hasTextPayload(element)) {
        invariant(
          element.classification.candidateKind === "text",
          `element ${element.id} raster text payload has a non-text candidate kind`
        );
        eligibleIds.push(element.id);
        oneOf(element.text.role, ["title", "body", "numeric", "caption"], `element ${element.id} text.role`);
        invariant(typeof element.text.plainText === "string", `element ${element.id} text.plainText is invalid`);
        assertStringArray(element.text.paragraphs, `element ${element.id} text.paragraphs`);
        assertTextStyle(element.text.style, `element ${element.id} text.style`);
        invariant(Array.isArray(element.text.runs), `element ${element.id} text.runs must be an array`);
        element.text.runs.forEach((run, runIndex) => {
          invariant(typeof run.text === "string", `element ${element.id} run ${runIndex} text is invalid`);
          assertBounds(run.bounds, `element ${element.id} run ${runIndex}.bounds`);
          invariant(Array.isArray(run.fragments), `element ${element.id} run ${runIndex}.fragments must be an array`);
          run.fragments.forEach((fragment, fragmentIndex) =>
            assertBounds(fragment, `element ${element.id} run ${runIndex}.fragments[${fragmentIndex}]`)
          );
          assertTextStyle(run.style, `element ${element.id} run ${runIndex}.style`);
        });
        if (element.text.containerShape) {
          assertBounds(element.text.containerShape.bounds, `element ${element.id} text.containerShape.bounds`);
          assertShapePayload(element.text.containerShape.shape, `element ${element.id} text.containerShape.shape`);
        }
      } else if (hasShapePayload(element)) {
        invariant(
          element.classification.candidateKind === "shape",
          `element ${element.id} raster shape payload has a non-shape candidate kind`
        );
        assertShapePayload(element.shape, `element ${element.id} shape`);
        if (isEditableRasterShapePayload(element)) eligibleIds.push(element.id);
      }
    }
  });

  invariant(slide.backplate?.strategy === "hide-native-leaves", "backplate.strategy is invalid");
  invariant(slide.backplate.mediaType === "image/png", "backplate.mediaType is invalid");
  invariant(
    slide.backplate.widthPx === AUTHORED_SLIDE_WIDTH_PX &&
      slide.backplate.heightPx === AUTHORED_SLIDE_HEIGHT_PX &&
      slide.backplate.transparentBackground === true,
    "backplate dimensions/transparency are invalid"
  );
  invariant(
    JSON.stringify(slide.backplate.eligibleElementIds) === JSON.stringify(eligibleIds),
    "backplate eligible IDs do not match promotable elements"
  );
  invariant(
    JSON.stringify(slide.backplate.rasterElementIds) === JSON.stringify(rasterIds),
    "backplate raster IDs do not match fallback elements"
  );
  invariant(Array.isArray(slide.warnings) && slide.warnings.every((warning) => typeof warning === "string"), "warnings must be strings");
  invariant(typeof JSON.stringify(slide) === "string", "contract must be JSON serializable");
}

export function serializeAuthoredHybridSlide(
  slide: AuthoredHybridSlideV1
): string {
  assertAuthoredHybridSlide(slide);
  return JSON.stringify(slide);
}
