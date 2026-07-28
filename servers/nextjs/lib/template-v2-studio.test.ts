import assert from "node:assert/strict";
import test from "node:test";

import {
  EMPTY_TEMPLATE_V2_STUDIO_STATE,
  createTemplateV2Rectangle,
  createTemplateV2SaveGate,
  createTemplateV2StudioCommandFacade,
  getSelectedElement,
  getTemplateV2Scene,
  isTemplateV2ElementLocked,
  isTemplateV2StudioEnabled,
  normalizeTemplateV2SelectionSet,
  templateV2StudioReducer,
  type JsonRecord,
  type StudioSelection,
} from "./template-v2-studio.ts";
import {
  fitTemplateV2Viewport,
  layoutTemplateV2TextRuns,
  normalizeElementGeometry,
  preserveTemplateV2ViewportOnResize,
  zoomTemplateV2Viewport,
} from "./template-v2-konva.ts";
import { templateV2VariantDigest } from "./template-v2-slide-variants.ts";

function fixture(): JsonRecord {
  return {
    layouts: [
      {
        id: "layout-1",
        description: "First layout",
        components: [
          {
            id: "component-1",
            description: "First component",
            position: { x: 10, y: 20 },
            elements: [
              {
                type: "text",
                name: "title",
                decorative: false,
                min_length: 1,
                max_length: 120,
                position: { x: 5, y: 6 },
                size: { width: 400, height: 80 },
                runs: [
                  { text: "Old", font: { bold: true } },
                  { text: " title", font: { italic: true } },
                ],
              },
              {
                type: "group",
                name: "nested",
                position: { x: 400, y: 20 },
                size: { width: 200, height: 100 },
                children: [
                  {
                    type: "container",
                    position: { x: 2, y: 3 },
                    size: { width: 50, height: 40 },
                    child: null,
                  },
                ],
              },
            ],
          },
          {
            id: "component-2",
            description: "Second component",
            position: { x: 500, y: 300 },
            elements: [],
          },
        ],
      },
      {
        id: "layout-2",
        description: "Second layout",
        components: [],
      },
    ],
  };
}

const titleSelection: StudioSelection = {
  layoutId: "layout-1",
  componentId: "component-1",
  elementPath: [0],
};

test("Template V2 Studio flag is strictly default-off", () => {
  assert.equal(isTemplateV2StudioEnabled(undefined), false);
  assert.equal(isTemplateV2StudioEnabled("false"), false);
  assert.equal(isTemplateV2StudioEnabled("TRUE"), false);
  assert.equal(isTemplateV2StudioEnabled(" true "), false);
  assert.equal(isTemplateV2StudioEnabled("true"), true);
});

test("save gate synchronously rejects concurrent saves", () => {
  const gate = createTemplateV2SaveGate();
  assert.equal(gate.tryAcquire(), true);
  assert.equal(gate.tryAcquire(), false);
  gate.release();
  assert.equal(gate.tryAcquire(), true);
});

test("load and navigation use stable layout and component ids without dirtying", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const switched = templateV2StudioReducer(loaded, {
    type: "set-active",
    layoutId: "layout-2",
    componentId: null,
  });

  assert.equal(loaded.activeLayoutId, "layout-1");
  assert.equal(loaded.activeComponentId, "component-1");
  assert.equal(switched.activeLayoutId, "layout-2");
  assert.equal(switched.activeComponentId, null);
  assert.equal(switched.dirty, false);
  assert.equal(getTemplateV2Scene(switched.layouts, "layout-2", null), null);
});

test("restoring a browser journal keeps the server snapshot and marks the draft dirty", () => {
  const serverLayouts = fixture();
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: serverLayouts,
  });
  const draftLayouts = structuredClone(serverLayouts);
  const draftElements = (
    ((draftLayouts.layouts as JsonRecord[])[0].components as JsonRecord[])[0]
      .elements as JsonRecord[]
  );
  draftElements.push(createTemplateV2Rectangle());

  const restored = templateV2StudioReducer(loaded, {
    type: "restore-draft",
    layouts: draftLayouts,
  });

  assert.equal(restored.layouts, draftLayouts);
  assert.equal(restored.savedLayouts, serverLayouts);
  assert.equal(restored.dirty, true);
  assert.equal(restored.past.length, 1);
  assert.equal(restored.activeLayoutId, "layout-1");
  assert.equal(restored.activeComponentId, "component-1");
});

test("path update changes only a nested element and preserves group-only geometry", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const selection: StudioSelection = {
    layoutId: "layout-1",
    componentId: "component-1",
    elementPath: [1, "children", 0],
  };
  const updated = templateV2StudioReducer(loaded, {
    type: "update-element-geometry",
    selection,
    geometry: { x: 33, y: 44, width: 70, height: 60, rotation: 15 },
  });
  const element = getSelectedElement(updated.layouts, selection);

  assert.deepEqual(element?.position, { x: 33, y: 44 });
  assert.deepEqual(element?.size, { width: 70, height: 60 });
  assert.equal(element?.rotation, 15);
  assert.deepEqual(
    (
      ((updated.layouts?.layouts as JsonRecord[])[0].components as JsonRecord[])
    )[1],
    (
      ((fixture().layouts as JsonRecord[])[0].components as JsonRecord[])
    )[1]
  );
  assert.deepEqual(
    ((loaded.layouts?.layouts as JsonRecord[])[1]),
    ((fixture().layouts as JsonRecord[])[1])
  );
});

test("editing one text run preserves every other run and its metadata", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const updated = templateV2StudioReducer(loaded, {
    type: "edit-text-run",
    selection: titleSelection,
    runIndex: 0,
    text: "New",
  });
  const runs = getSelectedElement(updated.layouts, titleSelection)
    ?.runs as JsonRecord[];

  assert.deepEqual(runs[0], { text: "New", font: { bold: true } });
  assert.deepEqual(runs[1], {
    text: " title",
    font: { italic: true },
  });
});

test("content run edits participate in reducer history and dirty tracking", () => {
  const layouts: JsonRecord = {
    layouts: [
      {
        id: "layout",
        components: [
          {
            id: "component",
            elements: [
              {
                type: "text-list",
                items: [[{ text: "Old", font: { bold: true } }]],
              },
            ],
          },
        ],
      },
    ],
  };
  const selection: StudioSelection = {
    layoutId: "layout",
    componentId: "component",
    elementPath: [0],
  };
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts,
  });
  const updated = templateV2StudioReducer(loaded, {
    type: "edit-content-run",
    selection,
    target: { kind: "list-item", itemIndex: 0, runIndex: 0 },
    text: "New",
    historyKey: "content-1",
  });
  const element = getSelectedElement(updated.layouts, selection);

  assert.deepEqual(element?.items, [
    [{ text: "New", font: { bold: true } }],
  ]);
  assert.equal(updated.dirty, true);
  assert.equal(updated.past.length, 1);
  assert.equal(updated.lastHistoryKey, "content-1");
});

test("history coalesces one text transaction and keeps the save checkpoint", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const first = templateV2StudioReducer(loaded, {
    type: "edit-text-run",
    selection: titleSelection,
    runIndex: 0,
    text: "N",
    historyKey: "focus-1",
  });
  const second = templateV2StudioReducer(first, {
    type: "edit-text-run",
    selection: titleSelection,
    runIndex: 0,
    text: "New",
    historyKey: "focus-1",
  });
  const saving = templateV2StudioReducer(second, {
    type: "begin-save",
    token: 1,
    layouts: second.layouts as JsonRecord,
  });
  const saved = templateV2StudioReducer(saving, {
    type: "save-succeeded",
    token: 1,
    layouts: structuredClone(second.layouts) as JsonRecord,
  });
  const undone = templateV2StudioReducer(saved, { type: "undo" });
  const redone = templateV2StudioReducer(undone, { type: "redo" });

  assert.equal(second.past.length, 1);
  assert.equal(saved.dirty, false);
  assert.equal(undone.dirty, true);
  assert.equal(redone.dirty, false);
});

test("a slow save response never overwrites edits made after its snapshot", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const requested = templateV2StudioReducer(loaded, {
    type: "edit-text-run",
    selection: titleSelection,
    runIndex: 0,
    text: "Sent to server",
  });
  const saving = templateV2StudioReducer(requested, {
    type: "begin-save",
    token: 41,
    layouts: requested.layouts as JsonRecord,
  });
  const editedAgain = templateV2StudioReducer(saving, {
    type: "edit-text-run",
    selection: titleSelection,
    runIndex: 0,
    text: "Local edit after request",
  });
  const completed = templateV2StudioReducer(editedAgain, {
    type: "save-succeeded",
    token: 41,
    layouts: structuredClone(requested.layouts) as JsonRecord,
  });

  assert.equal(
    (getSelectedElement(completed.layouts, titleSelection)?.runs as JsonRecord[])[0]
      .text,
    "Local edit after request"
  );
  assert.equal(completed.savedLayouts, requested.layouts);
  assert.equal(completed.pendingSave, null);
  assert.equal(completed.dirty, true);
});

test("undo and redo after a save request remain safe when the response arrives", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const requested = templateV2StudioReducer(loaded, {
    type: "edit-text-run",
    selection: titleSelection,
    runIndex: 0,
    text: "Sent to server",
  });
  const saving = templateV2StudioReducer(requested, {
    type: "begin-save",
    token: 7,
    layouts: requested.layouts as JsonRecord,
  });
  const undone = templateV2StudioReducer(saving, { type: "undo" });
  const completed = templateV2StudioReducer(undone, {
    type: "save-succeeded",
    token: 7,
    layouts: structuredClone(requested.layouts) as JsonRecord,
  });
  const redone = templateV2StudioReducer(completed, { type: "redo" });

  assert.equal(
    (getSelectedElement(completed.layouts, titleSelection)?.runs as JsonRecord[])[0]
      .text,
    "Old"
  );
  assert.equal(completed.dirty, true);
  assert.equal(
    (getSelectedElement(redone.layouts, titleSelection)?.runs as JsonRecord[])[0]
      .text,
    "Sent to server"
  );
  assert.equal(redone.dirty, false);
});

test("stale save tokens cannot clear or replace the active save snapshot", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const edited = templateV2StudioReducer(loaded, {
    type: "edit-text-run",
    selection: titleSelection,
    runIndex: 0,
    text: "Current",
  });
  const saving = templateV2StudioReducer(edited, {
    type: "begin-save",
    token: 9,
    layouts: edited.layouts as JsonRecord,
  });
  const stale = templateV2StudioReducer(saving, {
    type: "save-succeeded",
    token: 8,
    layouts: fixture(),
  });

  assert.equal(stale, saving);
  assert.equal(stale.pendingSave?.token, 9);
});

test("rectangle add selects the appended schema-compatible container", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const rectangle = createTemplateV2Rectangle();
  const updated = templateV2StudioReducer(loaded, {
    type: "add-rectangle",
    layoutId: "layout-1",
    componentId: "component-1",
    element: rectangle,
  });

  assert.deepEqual(getSelectedElement(updated.layouts, updated.selection), rectangle);
  assert.deepEqual(updated.selection?.elementPath, [2]);
});

test("selection sets are deduplicated, ordered, and constrained to one sibling parent", () => {
  const nested: StudioSelection = {
    layoutId: "layout-1",
    componentId: "component-1",
    elementPath: [1, "children", 0],
  };
  assert.deepEqual(
    normalizeTemplateV2SelectionSet([
      { ...titleSelection, elementPath: [1] },
      titleSelection,
      titleSelection,
    ]).map((selection) => selection.elementPath),
    [[0], [1]]
  );
  assert.deepEqual(
    normalizeTemplateV2SelectionSet([titleSelection, nested]),
    []
  );

  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const rejected = templateV2StudioReducer(loaded, {
    type: "set-selection",
    selections: [titleSelection, nested],
  });
  assert.equal(rejected.selection, null);
  assert.deepEqual(rejected.selectionSet, []);
});

test("geometry batch is atomic, lock-aware, and records one history snapshot", () => {
  const groupSelection: StudioSelection = {
    ...titleSelection,
    elementPath: [1],
  };
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const updated = templateV2StudioReducer(loaded, {
    type: "update-geometry-batch",
    updates: [
      {
        selection: titleSelection,
        geometry: { x: 20, y: 30, width: 410, height: 90, rotation: 5 },
      },
      {
        selection: groupSelection,
        geometry: { x: 500, y: 40, rotation: 30 },
      },
    ],
  });

  assert.equal(updated.past.length, 1);
  assert.deepEqual(getSelectedElement(updated.layouts, titleSelection)?.position, {
    x: 20,
    y: 30,
  });
  assert.deepEqual(getSelectedElement(updated.layouts, groupSelection)?.position, {
    x: 500,
    y: 40,
  });
  assert.equal(getSelectedElement(updated.layouts, groupSelection)?.rotation, undefined);

  const locked = templateV2StudioReducer(updated, {
    type: "set-element-lock",
    selection: titleSelection,
    locked: true,
  });
  const blocked = templateV2StudioReducer(locked, {
    type: "update-geometry-batch",
    updates: [
      { selection: titleSelection, geometry: { x: 999, y: 999 } },
      { selection: groupSelection, geometry: { x: 888, y: 888 } },
    ],
  });
  assert.equal(blocked, locked);
  assert.equal(blocked.past.length, 1);
  assert.equal(isTemplateV2ElementLocked(blocked, titleSelection), true);
  assert.equal(
    JSON.stringify(blocked.layouts).includes("lockedElementKeys"),
    false
  );
});

test("geometry batch translates vector points without inventing position fields", () => {
  const value = fixture();
  const component = (
    ((value.layouts as JsonRecord[])[0].components as JsonRecord[])
  )[0];
  (component.elements as JsonRecord[]).push({
    type: "vector",
    points: [{ x: 100, y: 50 }, { x: 300, y: 150 }],
  });
  const selection: StudioSelection = {
    ...titleSelection,
    elementPath: [2],
  };
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: value,
  });
  const updated = templateV2StudioReducer(loaded, {
    type: "update-geometry-batch",
    updates: [
      {
        selection,
        geometry: { x: 110, y: 45, translateX: 10, translateY: -5 },
      },
    ],
  });
  const vector = getSelectedElement(updated.layouts, selection);

  assert.deepEqual(vector?.points, [
    { x: 110, y: 45 },
    { x: 310, y: 145 },
  ]);
  assert.equal(vector?.position, undefined);
  assert.equal(updated.past.length, 1);
});

test("geometry and text no-ops preserve identity, history, and unknown geometry fields", () => {
  const value = fixture();
  const component = (
    ((value.layouts as JsonRecord[])[0].components as JsonRecord[])
  )[0];
  const title = (component.elements as JsonRecord[])[0];
  title.position = { x: 5, y: 6, coordinate_space: "slide" };
  title.size = { width: 400, height: 80, unit: "px" };
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: value,
  });
  const batchNoOp = templateV2StudioReducer(loaded, {
    type: "update-geometry-batch",
    updates: [
      {
        selection: titleSelection,
        geometry: { x: 5, y: 6, width: 400, height: 80 },
      },
    ],
  });
  const legacyNoOp = templateV2StudioReducer(batchNoOp, {
    type: "update-element-geometry",
    selection: titleSelection,
    geometry: { x: 5, y: 6, width: 400, height: 80 },
  });
  const textNoOp = templateV2StudioReducer(legacyNoOp, {
    type: "edit-text-run",
    selection: titleSelection,
    runIndex: 0,
    text: "Old",
  });
  const componentNoOp = templateV2StudioReducer(textNoOp, {
    type: "move-component",
    layoutId: "layout-1",
    componentId: "component-1",
    x: 10,
    y: 20,
  });
  const updated = templateV2StudioReducer(componentNoOp, {
    type: "update-geometry-batch",
    updates: [
      {
        selection: titleSelection,
        geometry: { x: 15, y: 16, width: 410, height: 90 },
      },
    ],
  });
  const updatedTitle = getSelectedElement(updated.layouts, titleSelection);

  assert.equal(batchNoOp, loaded);
  assert.equal(legacyNoOp, loaded);
  assert.equal(textNoOp, loaded);
  assert.equal(componentNoOp, loaded);
  assert.equal(updated.past.length, 1);
  assert.deepEqual(updatedTitle?.position, {
    x: 15,
    y: 16,
    coordinate_space: "slide",
  });
  assert.deepEqual(updatedTitle?.size, {
    width: 410,
    height: 90,
    unit: "px",
  });

  const invalidPartialSize = templateV2StudioReducer(updated, {
    type: "update-geometry-batch",
    updates: [
      {
        selection: titleSelection,
        geometry: { x: 99, y: 99, width: 100 },
      },
    ],
  });
  assert.equal(invalidPartialSize, updated);
});

function nestedOperationsFixture(): JsonRecord {
  const value = fixture();
  const component = (
    ((value.layouts as JsonRecord[])[0].components as JsonRecord[])
  )[0];
  const outerGroup = (component.elements as JsonRecord[])[1];
  outerGroup.children = [
    {
      type: "container",
      name: "first",
      position: { x: 2, y: 3 },
      size: { width: 50, height: 40 },
      child: null,
    },
    {
      type: "group",
      name: "inner",
      position: { x: 60, y: 3 },
      size: { width: 50, height: 40 },
      children: [
        {
          type: "container",
          position: { x: 0, y: 0 },
          size: { width: 20, height: 20 },
          child: null,
        },
      ],
    },
    {
      type: "container",
      name: "last",
      position: { x: 120, y: 3 },
      size: { width: 50, height: 40 },
      child: null,
    },
  ];
  return value;
}

test("an ancestor lock rejects child selection and every subtree command", () => {
  const parent: StudioSelection = {
    ...titleSelection,
    elementPath: [1],
  };
  const firstChild: StudioSelection = {
    ...titleSelection,
    elementPath: [1, "children", 0],
  };
  const innerGroup: StudioSelection = {
    ...titleSelection,
    elementPath: [1, "children", 1],
  };
  const lastChild: StudioSelection = {
    ...titleSelection,
    elementPath: [1, "children", 2],
  };
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: nestedOperationsFixture(),
  });
  const selectedParent = templateV2StudioReducer(loaded, {
    type: "set-selection",
    selections: [parent],
  });
  const locked = templateV2StudioReducer(selectedParent, {
    type: "set-element-lock",
    selection: parent,
    locked: true,
  });

  assert.equal(isTemplateV2ElementLocked(locked, firstChild), true);
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "set-selection",
      selections: [firstChild],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "update-geometry-batch",
      updates: [
        { selection: firstChild, geometry: { x: 99, y: 101 } },
      ],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "update-element-geometry",
      selection: firstChild,
      geometry: { x: 99, y: 101 },
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "reorder-siblings",
      direction: "front",
      selections: [firstChild],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "group-siblings",
      selections: [firstChild, lastChild],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "ungroup",
      selections: [innerGroup],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "align-siblings",
      direction: "left",
      selections: [firstChild, lastChild],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "distribute-siblings",
      direction: "horizontal",
      selections: [firstChild, innerGroup, lastChild],
    }),
    locked
  );
});

test("a descendant lock rejects operations that move or reshape its subtree", () => {
  const parent: StudioSelection = {
    ...titleSelection,
    elementPath: [1],
  };
  const child: StudioSelection = {
    ...titleSelection,
    elementPath: [1, "children", 0],
  };
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: nestedOperationsFixture(),
  });
  const selectedParent = templateV2StudioReducer(loaded, {
    type: "set-selection",
    selections: [parent],
  });
  const locked = templateV2StudioReducer(selectedParent, {
    type: "set-element-lock",
    selection: child,
    locked: true,
  });

  assert.equal(
    templateV2StudioReducer(locked, {
      type: "update-geometry-batch",
      updates: [{ selection: parent, geometry: { x: 500, y: 200 } }],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "reorder-siblings",
      direction: "back",
      selections: [parent],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "group-siblings",
      selections: [titleSelection, parent],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "ungroup",
      selections: [parent],
    }),
    locked
  );
  assert.equal(
    templateV2StudioReducer(locked, {
      type: "align-siblings",
      direction: "left",
      selections: [titleSelection, parent],
    }),
    locked
  );
});

function orderingFixture(): JsonRecord {
  const value = fixture();
  const component = (
    ((value.layouts as JsonRecord[])[0].components as JsonRecord[])
  )[0];
  component.elements = ["A", "B", "C", "D"].map((name, index) => ({
    type: "container",
    name,
    position: { x: index * 10, y: 0 },
    size: { width: 5, height: 5 },
    child: null,
  }));
  return value;
}

function alignmentFixture(): JsonRecord {
  const value = fixture();
  const component = (
    ((value.layouts as JsonRecord[])[0].components as JsonRecord[])
  )[0];
  component.elements = [
    {
      type: "container",
      name: "A",
      position: { x: 0, y: 10, coordinate_space: "slide" },
      size: { width: 20, height: 10, unit: "px" },
      child: null,
      custom: { retained: true },
    },
    {
      type: "container",
      name: "B",
      position: { x: 40, y: 40 },
      size: { width: 10, height: 30 },
      child: null,
    },
    {
      type: "container",
      name: "C",
      position: { x: 100, y: 80 },
      size: { width: 30, height: 20 },
      child: null,
    },
  ];
  return value;
}

function selectAllAlignmentElements(layouts: JsonRecord) {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts,
  });
  return templateV2StudioReducer(loaded, {
    type: "set-selection",
    selections: [0, 1, 2].map((index) => ({
      ...titleSelection,
      elementPath: [index],
    })),
  });
}

function elementNames(state: typeof EMPTY_TEMPLATE_V2_STUDIO_STATE): string[] {
  return (
    getTemplateV2Scene(state.layouts, "layout-1", "component-1")?.elements ?? []
  ).map((element) => String(element.name));
}

test("sibling reorder commands preserve relative order and remap selection and locks", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: orderingFixture(),
  });
  const selected = templateV2StudioReducer(loaded, {
    type: "set-selection",
    selections: [
      { ...titleSelection, elementPath: [0] },
      { ...titleSelection, elementPath: [2] },
    ],
  });
  const locked = templateV2StudioReducer(selected, {
    type: "set-element-lock",
    selection: { ...titleSelection, elementPath: [3] },
    locked: true,
  });
  const front = templateV2StudioReducer(locked, {
    type: "reorder-siblings",
    direction: "front",
  });

  assert.deepEqual(elementNames(front), ["B", "D", "A", "C"]);
  assert.deepEqual(
    front.selectionSet.map((selection) => selection.elementPath),
    [[2], [3]]
  );
  assert.equal(
    isTemplateV2ElementLocked(front, { ...titleSelection, elementPath: [1] }),
    true
  );
  assert.equal(front.past.length, 1);

  const single = templateV2StudioReducer(loaded, {
    type: "set-selection",
    selections: [{ ...titleSelection, elementPath: [1] }],
  });
  const forward = templateV2StudioReducer(single, {
    type: "reorder-siblings",
    direction: "forward",
  });
  const backward = templateV2StudioReducer(single, {
    type: "reorder-siblings",
    direction: "backward",
  });
  assert.deepEqual(elementNames(forward), ["A", "C", "B", "D"]);
  assert.deepEqual(elementNames(backward), ["B", "A", "C", "D"]);
});

test("alignment commands cover six axes atomically and preserve schema metadata", () => {
  const cases = [
    ["left", [[0, 10], [0, 40], [0, 80]]],
    ["center", [[55, 10], [60, 40], [50, 80]]],
    ["right", [[110, 10], [120, 40], [100, 80]]],
    ["top", [[0, 10], [40, 10], [100, 10]]],
    ["middle", [[0, 50], [40, 40], [100, 45]]],
    ["bottom", [[0, 90], [40, 70], [100, 80]]],
  ] as const;

  for (const [direction, expected] of cases) {
    const selected = selectAllAlignmentElements(alignmentFixture());
    const aligned = templateV2StudioReducer(selected, {
      type: "align-siblings",
      direction,
    });
    const elements =
      getTemplateV2Scene(
        aligned.layouts,
        "layout-1",
        "component-1"
      )?.elements ?? [];
    assert.deepEqual(
      elements.map((element) => {
        const position = element.position as JsonRecord;
        return [position.x, position.y];
      }),
      expected,
      direction
    );
    assert.equal(aligned.past.length, 1);
    assert.deepEqual(elements[0].custom, { retained: true });
    assert.equal((elements[0].position as JsonRecord).coordinate_space, "slide");
    assert.equal((elements[0].size as JsonRecord).unit, "px");
    assert.deepEqual(
      templateV2StudioReducer(aligned, { type: "undo" }).layouts,
      selected.layouts
    );
  }
});

test("horizontal and vertical distribution keep outer bounds and use even gaps", () => {
  const horizontalValue = alignmentFixture();
  const horizontalComponent = (
    ((horizontalValue.layouts as JsonRecord[])[0].components as JsonRecord[])
  )[0];
  const horizontalElements = horizontalComponent.elements as JsonRecord[];
  horizontalElements[1] = {
    ...horizontalElements[1],
    position: { x: 20, y: 40 },
  };
  horizontalElements[2] = {
    ...horizontalElements[2],
    position: { x: 50, y: 80 },
    size: { width: 10, height: 20 },
  };
  const horizontalSelected = selectAllAlignmentElements(horizontalValue);
  const horizontal = templateV2StudioReducer(horizontalSelected, {
    type: "distribute-siblings",
    direction: "horizontal",
  });
  assert.deepEqual(
    getTemplateV2Scene(
      horizontal.layouts,
      "layout-1",
      "component-1"
    )?.elements.map((element) => (element.position as JsonRecord).x),
    [0, 30, 50]
  );
  assert.equal(horizontal.past.length, 1);

  const verticalValue = alignmentFixture();
  const verticalComponent = (
    ((verticalValue.layouts as JsonRecord[])[0].components as JsonRecord[])
  )[0];
  const verticalElements = verticalComponent.elements as JsonRecord[];
  verticalElements[0] = {
    ...verticalElements[0],
    position: { x: 0, y: 0 },
    size: { width: 20, height: 10 },
  };
  verticalElements[1] = {
    ...verticalElements[1],
    position: { x: 40, y: 20 },
    size: { width: 10, height: 20 },
  };
  verticalElements[2] = {
    ...verticalElements[2],
    position: { x: 100, y: 70 },
    size: { width: 30, height: 10 },
  };
  const verticalSelected = selectAllAlignmentElements(verticalValue);
  const vertical = templateV2StudioReducer(verticalSelected, {
    type: "distribute-siblings",
    direction: "vertical",
  });
  assert.deepEqual(
    getTemplateV2Scene(
      vertical.layouts,
      "layout-1",
      "component-1"
    )?.elements.map((element) => (element.position as JsonRecord).y),
    [0, 30, 70]
  );
  assert.equal(vertical.past.length, 1);
});

test("alignment and distribution no-ops do not dirty or pollute history", () => {
  const value = alignmentFixture();
  const component = (
    ((value.layouts as JsonRecord[])[0].components as JsonRecord[])
  )[0];
  const elements = component.elements as JsonRecord[];
  elements.forEach((element, index) => {
    element.position = { x: index * 30, y: 10 };
    element.size = { width: 10, height: 10 };
  });
  const selected = selectAllAlignmentElements(value);
  const aligned = templateV2StudioReducer(selected, {
    type: "align-siblings",
    direction: "top",
  });
  const distributed = templateV2StudioReducer(aligned, {
    type: "distribute-siblings",
    direction: "horizontal",
  });
  const tooFew = templateV2StudioReducer(
    templateV2StudioReducer(selected, {
      type: "set-selection",
      selections: [0, 1].map((index) => ({
        ...titleSelection,
        elementPath: [index],
      })),
    }),
    { type: "distribute-siblings", direction: "vertical" }
  );

  assert.equal(aligned, selected);
  assert.equal(distributed, selected);
  assert.equal(tooFew.past.length, 0);
  assert.equal(tooFew.dirty, false);
});

test("undo and redo preserve session locks at their remapped paths", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: orderingFixture(),
  });
  const selected = templateV2StudioReducer(loaded, {
    type: "set-selection",
    selections: [
      { ...titleSelection, elementPath: [0] },
      { ...titleSelection, elementPath: [2] },
    ],
  });
  const locked = templateV2StudioReducer(selected, {
    type: "set-element-lock",
    selection: { ...titleSelection, elementPath: [3] },
    locked: true,
  });
  const reordered = templateV2StudioReducer(locked, {
    type: "reorder-siblings",
    direction: "front",
  });
  const undone = templateV2StudioReducer(reordered, { type: "undo" });
  const redone = templateV2StudioReducer(undone, { type: "redo" });

  assert.deepEqual(elementNames(reordered), ["B", "D", "A", "C"]);
  assert.equal(
    isTemplateV2ElementLocked(reordered, {
      ...titleSelection,
      elementPath: [1],
    }),
    true
  );
  assert.deepEqual(elementNames(undone), ["A", "B", "C", "D"]);
  assert.equal(
    isTemplateV2ElementLocked(undone, {
      ...titleSelection,
      elementPath: [3],
    }),
    true
  );
  assert.equal(
    isTemplateV2ElementLocked(undone, {
      ...titleSelection,
      elementPath: [1],
    }),
    false
  );
  assert.deepEqual(elementNames(redone), ["B", "D", "A", "C"]);
  assert.equal(
    isTemplateV2ElementLocked(redone, {
      ...titleSelection,
      elementPath: [1],
    }),
    true
  );
  assert.equal(
    JSON.stringify(redone.layouts).includes("lockedElementKeys"),
    false
  );
  assert.equal(
    redone.past.some((layouts) => "lockedElementKeys" in layouts),
    false
  );
});

test("locks changed after a content edit remain session-stable across history", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const edited = templateV2StudioReducer(loaded, {
    type: "edit-text-run",
    selection: titleSelection,
    runIndex: 0,
    text: "Edited",
  });
  const lockedAfterEdit = templateV2StudioReducer(edited, {
    type: "set-element-lock",
    selection: titleSelection,
    locked: true,
  });
  const undone = templateV2StudioReducer(lockedAfterEdit, { type: "undo" });
  const redone = templateV2StudioReducer(undone, { type: "redo" });

  assert.equal(isTemplateV2ElementLocked(undone, titleSelection), true);
  assert.equal(isTemplateV2ElementLocked(redone, titleSelection), true);
});

test("group and ungroup use a bounding box, local coordinates, and remapped paths", () => {
  const value = fixture();
  const component = (
    ((value.layouts as JsonRecord[])[0].components as JsonRecord[])
  )[0];
  const title = (component.elements as JsonRecord[])[0];
  title.position = {
    ...(title.position as JsonRecord),
    coordinate_space: "slide",
  };
  (component.elements as JsonRecord[]).push({
    type: "container",
    name: "tail",
    position: { x: 700, y: 10 },
    size: { width: 20, height: 20 },
    child: null,
  });
  const originalElements = structuredClone(component.elements);
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: value,
  });
  const selected = templateV2StudioReducer(loaded, {
    type: "set-selection",
    selections: [titleSelection, { ...titleSelection, elementPath: [1] }],
  });
  const tailLocked = templateV2StudioReducer(selected, {
    type: "set-element-lock",
    selection: { ...titleSelection, elementPath: [2] },
    locked: true,
  });
  const grouped = templateV2StudioReducer(tailLocked, {
    type: "group-siblings",
    name: "selection",
  });
  const group = getSelectedElement(grouped.layouts, grouped.selection);

  assert.equal(group?.type, "group");
  assert.equal(group?.name, "selection");
  assert.deepEqual(group?.position, { x: 5, y: 6 });
  assert.deepEqual(group?.size, { width: 595, height: 114 });
  assert.deepEqual(
    (group?.children as JsonRecord[]).map((child) => child.position),
    [
      { x: 0, y: 0, coordinate_space: "slide" },
      { x: 395, y: 14 },
    ]
  );
  assert.equal(
    isTemplateV2ElementLocked(grouped, {
      ...titleSelection,
      elementPath: [1],
    }),
    true
  );
  assert.equal(grouped.past.length, 1);

  const ungrouped = templateV2StudioReducer(grouped, { type: "ungroup" });
  assert.deepEqual(
    getTemplateV2Scene(
      ungrouped.layouts,
      "layout-1",
      "component-1"
    )?.elements,
    originalElements
  );
  assert.deepEqual(
    ungrouped.selectionSet.map((selection) => selection.elementPath),
    [[0], [1]]
  );
  assert.equal(ungrouped.past.length, 2);
});

test("command history is capped at forty snapshots", () => {
  let state = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  for (let index = 1; index <= 45; index += 1) {
    state = templateV2StudioReducer(state, {
      type: "update-geometry-batch",
      updates: [
        {
          selection: titleSelection,
          geometry: { x: 100 + index, y: 6 },
        },
      ],
    });
  }
  assert.equal(state.past.length, 40);
  assert.equal(state.pastSessionHistory.length, 40);
  for (let index = 0; index < 40; index += 1) {
    state = templateV2StudioReducer(state, { type: "undo" });
  }
  assert.equal(
    (getSelectedElement(state.layouts, titleSelection)?.position as JsonRecord).x,
    105
  );
  assert.equal(
    templateV2StudioReducer(state, { type: "undo" }),
    state
  );
});

test("command facade emits one execute action per command", () => {
  const actions: unknown[] = [];
  const commands = createTemplateV2StudioCommandFacade((action) =>
    actions.push(action)
  );
  commands.updateGeometryBatch([
    { selection: titleSelection, geometry: { x: 1, y: 2 } },
  ]);
  commands.reorderSiblings("back");
  commands.alignSiblings("center");
  commands.distributeSiblings("vertical");
  commands.groupSiblings(undefined, "group");
  commands.ungroup();

  assert.deepEqual(
    actions.map((action) => (action as JsonRecord).type),
    [
      "execute-command",
      "execute-command",
      "execute-command",
      "execute-command",
      "execute-command",
      "execute-command",
    ]
  );
  assert.deepEqual(
    actions.map(
      (action) =>
        ((action as JsonRecord).command as JsonRecord).type
    ),
    [
      "update-geometry-batch",
      "reorder-siblings",
      "align-siblings",
      "distribute-siblings",
      "group-siblings",
      "ungroup",
    ]
  );
});

test("viewport zoom stays anchored to the pointer", () => {
  const fitted = fitTemplateV2Viewport(1000, 600);
  const pointer = { x: 300, y: 250 };
  const logicalBefore = {
    x: (pointer.x - fitted.x) / fitted.scale,
    y: (pointer.y - fitted.y) / fitted.scale,
  };
  const zoomed = zoomTemplateV2Viewport(fitted, pointer, fitted.scale * 1.5);
  const logicalAfter = {
    x: (pointer.x - zoomed.x) / zoomed.scale,
    y: (pointer.y - zoomed.y) / zoomed.scale,
  };

  assert.deepEqual(logicalAfter, logicalBefore);
});

test("viewport resize preserves zoom and the logical point at its center", () => {
  const previousSize = { width: 1000, height: 600 };
  const nextSize = { width: 1320, height: 760 };
  const viewport = { scale: 1.75, x: -240, y: -110 };
  const logicalBefore = {
    x: (previousSize.width / 2 - viewport.x) / viewport.scale,
    y: (previousSize.height / 2 - viewport.y) / viewport.scale,
  };
  const resized = preserveTemplateV2ViewportOnResize(
    viewport,
    previousSize,
    nextSize
  );
  const logicalAfter = {
    x: (nextSize.width / 2 - resized.x) / resized.scale,
    y: (nextSize.height / 2 - resized.y) / resized.scale,
  };

  assert.equal(resized.scale, viewport.scale);
  assert.deepEqual(logicalAfter, logicalBefore);
});

test("text run layout inherits element font fields and keeps run overrides", () => {
  const layout = layoutTemplateV2TextRuns(
    {
      type: "text",
      size: { width: 500, height: 80 },
      fill: { color: "#111827", opacity: 0.8 },
      font: {
        family: "Inter",
        size: 28,
        color: "#1d4ed8",
        bold: true,
        italic: true,
        underline: true,
      },
      runs: [
        { text: "Base " },
        {
          text: "override",
          font: {
            family: "Georgia",
            size: 34,
            color: "#dc2626",
            bold: false,
            underline: false,
          },
        },
      ],
    },
    500,
    (run) => run.text.length * 10
  );

  assert.equal(layout.mode, "runs");
  if (layout.mode !== "runs") return;
  assert.deepEqual(
    layout.runs.map(
      ({
        text,
        x,
        width,
        fontFamily,
        fontSize,
        fontStyle,
        fill,
        textDecoration,
      }) => ({
        text,
        x,
        width,
        fontFamily,
        fontSize,
        fontStyle,
        fill,
        textDecoration,
      })
    ),
    [
      {
        text: "Base ",
        x: 0,
        width: 50,
        fontFamily: "Inter",
        fontSize: 28,
        fontStyle: "bold italic",
        fill: "#1d4ed8",
        textDecoration: "underline",
      },
      {
        text: "override",
        x: 50,
        width: 80,
        fontFamily: "Georgia",
        fontSize: 34,
        fontStyle: "italic",
        fill: "#dc2626",
        textDecoration: "",
      },
    ]
  );
});

test("text run layout falls back losslessly for multiline and overflow content", () => {
  const element = {
    type: "text",
    font: { family: "Inter", size: 24, bold: true },
    runs: [
      { text: "First\n", font: { color: "#ef4444" } },
      { text: "Second", font: { italic: true } },
    ],
  };
  const multiline = layoutTemplateV2TextRuns(element, 500, () => 20);
  const overflow = layoutTemplateV2TextRuns(
    { ...element, runs: [{ text: "First" }, { text: "Second" }] },
    30,
    () => 20
  );

  assert.equal(multiline.mode, "fallback");
  assert.equal(overflow.mode, "fallback");
  if (multiline.mode === "fallback") {
    assert.equal(multiline.reason, "multiline");
    assert.equal(multiline.text, "First\nSecond");
  }
  if (overflow.mode === "fallback") {
    assert.equal(overflow.reason, "overflow");
    assert.equal(overflow.text, "FirstSecond");
  }
});

test("transform normalization folds scale and excludes unsupported group rotation", () => {
  assert.deepEqual(
    normalizeElementGeometry(
      { type: "container" },
      {
        x: 10.125,
        y: 20.456,
        width: 100,
        height: 40,
        scaleX: 1.5,
        scaleY: 2,
        rotation: -15,
      }
    ),
    { x: 10.13, y: 20.46, width: 150, height: 80, rotation: 345 }
  );
  assert.deepEqual(
    normalizeElementGeometry(
      { type: "group" },
      { x: 2, y: 3, width: 100, height: 40, scaleX: 2, rotation: 20 }
    ),
    { x: 2, y: 3 }
  );
});

test("bounded element apply enforces revision, digest, locks, and undo history", () => {
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: fixture(),
  });
  const current = getSelectedElement(loaded.layouts, titleSelection);
  assert.ok(current);
  const replacement = {
    ...current,
    runs: [{ text: "Bounded", font: { bold: true } }],
    unknown_metadata: { retained: true },
  };
  const action = {
    type: "apply-bounded-element" as const,
    selection: titleSelection,
    replacement,
    expectedElementDigest: templateV2VariantDigest(current),
    expectedRevision: 7,
    currentRevision: 7,
    historyKey: "bounded-element:test",
  };

  const updated = templateV2StudioReducer(loaded, action);
  assert.equal(updated.dirty, true);
  assert.deepEqual(
    getSelectedElement(updated.layouts, titleSelection),
    replacement,
  );
  assert.deepEqual(
    getSelectedElement(
      templateV2StudioReducer(updated, { type: "undo" }).layouts,
      titleSelection,
    ),
    current,
  );

  const staleRevision = templateV2StudioReducer(loaded, {
    ...action,
    currentRevision: 8,
  });
  const staleDigest = templateV2StudioReducer(loaded, {
    ...action,
    expectedElementDigest: "changed",
  });
  const locked = templateV2StudioReducer(
    templateV2StudioReducer(loaded, {
      type: "set-element-lock",
      selection: titleSelection,
      locked: true,
    }),
    action,
  );
  assert.equal(staleRevision, loaded);
  assert.equal(staleDigest, loaded);
  assert.deepEqual(getSelectedElement(locked.layouts, titleSelection), current);
});
