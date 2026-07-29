import { templates } from "@/app/presentation-templates";
import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";

/** Default template for the compose screen — the content-first adaptive composer
 * (NotebookLM/Gamma-style; AI composes a fresh archetype per slide). Legacy
 * template groups remain selectable. */
export const DEFAULT_TEMPLATE_ID = "adaptive";

/**
 * Resolve a stored template id into the value that TemplateSelection and
 * usePresentationGeneration consume: the full object for a built-in template,
 * the id string for a custom template (custom-<uuid>), or null.
 *
 * Redux stores only the id because built-in templates carry React components
 * (non-serializable); this rehydrates the selection at the call site.
 */
export function resolveTemplateSelection(
  id: string | null
): TemplateLayoutsWithSettings | string | null {
  if (!id) return null;
  return templates.find((t) => t.id === id) ?? id;
}

/** Inverse of resolveTemplateSelection: TemplateSelection's value → storable id. */
export function templateSelectionToId(
  template: TemplateLayoutsWithSettings | string
): string {
  return typeof template === "string" ? template : template.id;
}
