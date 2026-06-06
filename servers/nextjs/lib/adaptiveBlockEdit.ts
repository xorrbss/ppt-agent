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

// --- Block / item CRUD (P4b) --- //
// A structural "unit" is either a top-level block (e.g. a card/stat/step block,
// or any block in blocks[]) or an item inside a block's items[] (bullets items,
// comparison column items). CRUD finds the array that owns a unit id and
// operates on it, so the non-uniform model (items[] vs repeated top-level
// blocks) is handled uniformly.

function allUnitIds(blocks: AdaptiveBlock[]): Set<string> {
  const ids = new Set<string>();
  for (const b of blocks) {
    if (b?.id) ids.add(b.id);
    if (Array.isArray(b?.items)) {
      for (const it of b.items) if (it?.id) ids.add(it.id);
    }
  }
  return ids;
}

function freshUnitId(blocks: AdaptiveBlock[], base: string): string {
  const ids = allUnitIds(blocks);
  const safeBase = base || "unit";
  let k = 1;
  let id = `${safeBase}-${k}`;
  while (ids.has(id)) {
    k += 1;
    id = `${safeBase}-${k}`;
  }
  return id;
}

/** A blank sibling of the same shape: keep type, fresh id, empty string fields,
 *  empty arrays, drop object fields (icon/image — re-added via the asset UI). */
function cloneEmptyUnit(sibling: AdaptiveBlock, freshId: string): AdaptiveBlock {
  const u: AdaptiveBlock = { ...sibling, id: freshId };
  for (const k of Object.keys(u)) {
    if (k === "id" || k === "type") continue;
    const v = (u as any)[k];
    if (typeof v === "string") (u as any)[k] = "";
    else if (Array.isArray(v)) (u as any)[k] = [];
    else delete (u as any)[k];
  }
  return u;
}

/** Locate the array (blocks[] or a block.items[]) + index that owns `id`. */
export function locateUnit(
  blocks: AdaptiveBlock[] | undefined,
  id: string
): { array: AdaptiveBlock[]; index: number } | null {
  if (!Array.isArray(blocks) || !id) return null;
  const top = blocks.findIndex((b) => b?.id === id);
  if (top >= 0) return { array: blocks, index: top };
  for (const b of blocks) {
    if (Array.isArray(b?.items)) {
      const j = b.items.findIndex((x: AdaptiveBlock) => x?.id === id);
      if (j >= 0) return { array: b.items, index: j };
    }
  }
  return null;
}

/** Delete the unit `id` from its owning array. Keeps ≥1 sibling. */
export function deleteAdaptiveUnit(blocks: AdaptiveBlock[] | undefined, id: string): boolean {
  const loc = locateUnit(blocks, id);
  if (!loc || loc.array.length <= 1) return false;
  loc.array.splice(loc.index, 1);
  return true;
}

/** Move the unit `id` by `delta` (±1) within its owning array. */
export function moveAdaptiveUnit(
  blocks: AdaptiveBlock[] | undefined,
  id: string,
  delta: number
): boolean {
  const loc = locateUnit(blocks, id);
  if (!loc) return false;
  const j = loc.index + delta;
  if (j < 0 || j >= loc.array.length) return false;
  const [u] = loc.array.splice(loc.index, 1);
  loc.array.splice(j, 0, u);
  return true;
}

/** Insert a new blank sibling after the unit `afterId`. Returns its new id. */
export function addAdaptiveUnit(
  blocks: AdaptiveBlock[] | undefined,
  afterId: string
): string | null {
  if (!Array.isArray(blocks)) return null;
  const loc = locateUnit(blocks, afterId);
  if (!loc) return null;
  const sibling = loc.array[loc.index];
  const base =
    (typeof sibling.type === "string" && sibling.type) ||
    (sibling.id || "unit").replace(/[._-]?\d+$/, "") ||
    "unit";
  const freshId = freshUnitId(blocks, base);
  loc.array.splice(loc.index + 1, 0, cloneEmptyUnit(sibling, freshId));
  return freshId;
}
