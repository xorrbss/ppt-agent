/**
 * The "safe style" form in AuthoredElementEditor exposes a curated subset of
 * the backend's `set_style` allowlist (servers/fastapi/utils/authored_patch_engine.py
 * `_STYLE_PROPERTIES`). Every kebab-case name below is a member of that
 * backend set; this widens the previous five-property client list to a
 * grouped set of properties that are safe to hand-edit as a single value in a
 * plain text input, while leaving out backend-only properties that are
 * structural/repair-machinery-oriented (e.g. `overflow`, `transform`,
 * `align-items`, `grid-row`) or already covered by the dedicated geometry
 * operation (`left`/`right`/`top`/`bottom`).
 *
 * Property names here are camelCase (the DOM/CSSStyleDeclaration convention);
 * AuthoredElementEditor converts to kebab-case before sending a patch.
 */

export interface AuthoredEditableStyleGroup {
  readonly groupLabel: string;
  readonly properties: readonly string[];
}

export const AUTHORED_EDITABLE_STYLE_GROUPS: readonly AuthoredEditableStyleGroup[] = [
  {
    groupLabel: "타이포그래피",
    properties: [
      "fontSize",
      "fontWeight",
      "lineHeight",
      "letterSpacing",
      "textAlign",
      "color",
    ],
  },
  {
    groupLabel: "박스",
    properties: [
      "backgroundColor",
      "borderRadius",
      "padding",
      "paddingTop",
      "paddingRight",
      "paddingBottom",
      "paddingLeft",
      "margin",
      "marginTop",
      "marginRight",
      "marginBottom",
      "marginLeft",
      "gap",
      "opacity",
    ],
  },
  {
    groupLabel: "레이아웃",
    properties: ["gridColumn", "gridTemplateColumns", "zIndex"],
  },
];

export const EDITABLE_STYLE_PROPERTIES: readonly string[] =
  AUTHORED_EDITABLE_STYLE_GROUPS.flatMap((group) => group.properties);
