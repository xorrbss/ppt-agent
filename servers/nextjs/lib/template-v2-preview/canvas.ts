import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import { cloneLayout, isRecord, type UnknownRecord } from "./core";

/**
 * Density content is synthetic, so only copy canvas/layout edits back into the
 * stored layout while a density preview is active.
 */
export function mergeDensityPreviewCanvasEdits(
  storedLayout: TemplateV2Layout,
  editedPreviewLayout: TemplateV2Layout,
) {
  const nextLayout = cloneLayout(storedLayout);
  const target = nextLayout as UnknownRecord;
  const source = editedPreviewLayout as UnknownRecord;

  syncComponentCanvasFields(target.components, source.components);
  syncElementCanvasFields(target.elements, source.elements);
  return nextLayout;
}

function syncComponentCanvasFields(
  targetComponents: unknown,
  sourceComponents: unknown,
) {
  if (!Array.isArray(targetComponents) || !Array.isArray(sourceComponents)) {
    return;
  }

  targetComponents.forEach((targetComponent, index) => {
    const sourceComponent = sourceComponents[index];
    if (!isRecord(targetComponent) || !isRecord(sourceComponent)) return;
    syncObjectFields(targetComponent, sourceComponent, [
      "position",
      "size",
      "rotation",
    ]);
    syncElementCanvasFields(targetComponent.elements, sourceComponent.elements);
  });
}

function syncElementCanvasFields(
  targetElements: unknown,
  sourceElements: unknown,
) {
  if (!Array.isArray(targetElements) || !Array.isArray(sourceElements)) return;

  targetElements.forEach((targetElement, index) => {
    const sourceElement = sourceElements[index];
    if (!isRecord(targetElement) || !isRecord(sourceElement)) return;
    syncObjectFields(targetElement, sourceElement, [
      "position",
      "size",
      "rotation",
      "points",
      "__presenton_manual_position",
    ]);
    syncElementCanvasFields(targetElement.children, sourceElement.children);
    syncElementCanvasFields(targetElement.elements, sourceElement.elements);
    if (isRecord(targetElement.child) && isRecord(sourceElement.child)) {
      syncElementCanvasFields([targetElement.child], [sourceElement.child]);
    }
  });
}

function syncObjectFields(
  target: UnknownRecord,
  source: UnknownRecord,
  fields: string[],
) {
  fields.forEach((field) => {
    if (field in source) target[field] = cloneLayout(source[field]);
    else delete target[field];
  });
}
