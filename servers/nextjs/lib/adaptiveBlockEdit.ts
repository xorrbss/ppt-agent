// Pure resolution for the adaptive block-id edit binding. Shared by the
// updateAdaptiveBlock reducer (write) and TiptapTextReplacer (read) so the two
// stay in lock-step and stay testable without React/Redux.
//
// A data-block-id is one of:
//   - a flat top-level text block id      e.g. "title", "eyebrow", "lead", "statement"
//   - a nested item id (bullets / columns) e.g. "b1", "a3", "col1.1"
//   - a dotted "blockId.field"             e.g. "s1.value", "card1.title", "step1.label"
//
// Resolution is by id only (never by text), so duplicate text across blocks can
// never misbind — the defect the old string-match findDataPath path had.

export interface AdaptiveBlock {
  id?: string;
  text?: string;
  items?: AdaptiveBlock[];
  [key: string]: any;
}

/** Write `content` to the leaf addressed by `blockId`. Returns true if applied. */
export function setAdaptiveBlockText(
  blocks: AdaptiveBlock[] | undefined,
  blockId: string,
  content: string
): boolean {
  if (!Array.isArray(blocks) || !blockId) return false;

  // 1) exact id match: a top-level text block, or an item in a block's array
  for (const b of blocks) {
    if (b && b.id === blockId && typeof b.text === "string") {
      b.text = content;
      return true;
    }
    if (b && Array.isArray(b.items)) {
      const item = b.items.find((x) => x && x.id === blockId);
      if (item) {
        item.text = content;
        return true;
      }
    }
  }

  // 2) dotted "parentId.field" — set the named scalar field on the block
  const dot = blockId.lastIndexOf(".");
  if (dot > 0) {
    const parentId = blockId.slice(0, dot);
    const field = blockId.slice(dot + 1);
    const b = blocks.find((x) => x && x.id === parentId);
    if (b) {
      b[field] = content;
      return true;
    }
  }
  return false;
}

/** Read the current value for `blockId` from a slide content object. */
export function getAdaptiveBlockText(
  content: any,
  blockId: string
): string | undefined {
  const blocks = content?.blocks;
  if (!Array.isArray(blocks) || !blockId) return undefined;

  for (const b of blocks) {
    if (b && b.id === blockId && typeof b.text === "string") return b.text;
    if (b && Array.isArray(b.items)) {
      const item = b.items.find((x: any) => x && x.id === blockId);
      if (item) return item.text;
    }
  }

  const dot = blockId.lastIndexOf(".");
  if (dot > 0) {
    const b = blocks.find((x: any) => x && x.id === blockId.slice(0, dot));
    if (b) return b[blockId.slice(dot + 1)];
  }
  return undefined;
}
