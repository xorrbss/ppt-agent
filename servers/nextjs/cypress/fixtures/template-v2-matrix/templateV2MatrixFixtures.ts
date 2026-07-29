/**
 * A deliberately small, source-backed cross-section of the Template V2 corpus.
 *
 * The first four entries identify the existing renderer fidelity corpus. The
 * remaining entries are verbatim element shapes from the pinned upstream
 * compatibility fixture (57b194b). `nested-lock-remap` combines the group and
 * rich-element patterns already exercised by that corpus so a Studio component
 * test can use one stable, interactive API envelope.
 */
export const templateV2MatrixCases = [
  {
    id: "title-body",
    source: "lib/export-fidelity/fixtures/template-v2-general/00-title-body/template.v2.json",
    capabilities: ["editable text", "save/reload", "PPT export"],
  },
  {
    id: "geometry",
    source: "lib/export-fidelity/fixtures/template-v2-general/01-geometry/template.v2.json",
    capabilities: ["sibling selection", "40-entry undo/redo", "PPT export"],
  },
  {
    id: "text-fit",
    source: "lib/export-fidelity/fixtures/template-v2-general/02-text-fit/template.v2.json",
    capabilities: ["continuous text history", "autosave coalescing", "PPT export"],
  },
  {
    id: "rich-elements",
    source: "lib/export-fidelity/fixtures/template-v2-general/03-rich-elements/template.v2.json",
    capabilities: ["nested selection", "groups", "charts/tables", "PPT export"],
  },
  {
    id: "upstream-text",
    source: "../fastapi/tests/fixtures/template_v2/upstream-elements-57b194b.json#text",
    capabilities: ["upstream extension retention", "shortcuts"],
  },
  {
    id: "upstream-image",
    source: "../fastapi/tests/fixtures/template_v2/upstream-elements-57b194b.json#image",
    capabilities: ["media selection", "portal toolbar cleanup"],
  },
  {
    id: "upstream-table",
    source: "../fastapi/tests/fixtures/template_v2/upstream-elements-57b194b.json#table",
    capabilities: ["nested cells", "optimistic persistence"],
  },
  {
    id: "nested-lock-remap",
    source: "lib/export-fidelity/fixtures/template-v2-general/03-rich-elements/template.v2.json#group",
    capabilities: ["ancestor/subtree locks", "selection remap", "a11y"],
  },
] as const;

export const templateV2MatrixTemplateId = "template-v2-matrix";

export function templateV2MatrixLayouts() {
  return {
    matrix_fixture_contract: "template-v2-e2e-matrix-v1",
    layouts: [
      {
        id: "matrix-rich-elements",
        source_fixture:
          "lib/export-fidelity/fixtures/template-v2-general/03-rich-elements/template.v2.json",
        components: [
          {
            id: "main",
            position: { x: 0, y: 0 },
            elements: [
              {
                type: "text",
                name: "title",
                position: { x: 40, y: 40 },
                size: { width: 400, height: 56 },
                font: { family: "Arial", size: 36, color: "#123b72", bold: true },
                runs: [{ text: "Renderer contract" }],
                decorative: false,
                min_length: 1,
                max_length: 120,
                upstream_element_extension: "text-vNext",
              },
              {
                type: "container",
                name: "panel",
                position: { x: 48, y: 136 },
                size: { width: 240, height: 120 },
                fill: { color: "#dbeafe", future_gradient: "linear" },
                upstream_element_extension: "container-vNext",
              },
              {
                type: "group",
                name: "group",
                position: { x: 360, y: 160 },
                size: { width: 260, height: 160 },
                children: [
                  {
                    type: "container",
                    name: "group-card",
                    position: { x: 0, y: 0 },
                    size: { width: 260, height: 160 },
                    fill: { color: "#fef3c7" },
                    child: {
                      type: "text",
                      name: "group-label",
                      position: { x: 0, y: 0 },
                      size: { width: 260, height: 160 },
                      font: { family: "Arial", size: 24, color: "#92400e", bold: true },
                      runs: [{ text: "Grouped" }],
                      decorative: false,
                      min_length: 1,
                      max_length: 40,
                    },
                  },
                ],
                upstream_element_extension: "group-vNext",
              },
              {
                type: "table",
                name: "metrics",
                position: { x: 680, y: 136 },
                size: { width: 300, height: 150 },
                columns: [{ runs: [{ text: "Metric" }] }],
                rows: [[{ runs: [{ text: "Revenue" }] }]],
                decorative: false,
                min_columns: 1,
                max_columns: 1,
                min_rows: 1,
                max_rows: 1,
                upstream_element_extension: "table-vNext",
              },
              {
                type: "image",
                name: "hero",
                data: "/app_data/images/hero.png",
                position: { x: 1040, y: 40 },
                size: { width: 120, height: 120 },
                decorative: false,
                is_icon: false,
                upstream_element_extension: "image-vNext",
              },
            ],
          },
        ],
      },
    ],
  };
}

export function templateV2MatrixResponse(layouts = templateV2MatrixLayouts(), revision = 1) {
  return {
    id: templateV2MatrixTemplateId,
    name: "Template V2 E2E matrix",
    description: "Source-backed Template V2 regression matrix",
    layouts,
    revision,
    updated_at: "2026-07-29T00:00:00Z",
  };
}
