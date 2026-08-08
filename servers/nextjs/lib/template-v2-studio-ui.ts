import {
  normalizeTemplateV2SelectionSet,
  templateV2SelectionKey,
  type StudioSelection,
} from "./template-v2-studio.ts";

export function toggleTemplateV2Selection(
  current: StudioSelection[],
  candidate: StudioSelection,
  additive: boolean
): StudioSelection[] {
  if (!additive) return [candidate];

  const candidateKey = templateV2SelectionKey(candidate);
  if (current.some((selection) => templateV2SelectionKey(selection) === candidateKey)) {
    return current.filter(
      (selection) => templateV2SelectionKey(selection) !== candidateKey
    );
  }

  if (current.length === 0) return [candidate];
  const normalized = normalizeTemplateV2SelectionSet([...current, candidate]);
  return normalized.length === current.length + 1 ? normalized : [candidate];
}
