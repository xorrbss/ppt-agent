import type {
  Bounds,
  DeckMasterContractV1,
  MasterSlot,
  MasterValidation,
  Ownership,
  ValidationIssue,
} from "./contracts.ts";
import { REQUIRED_LAYOUT_KINDS } from "./contracts.ts";

function ownershipIssues(
  item: { id: string; ownership: Ownership },
  expected: Ownership,
  path: string,
): ValidationIssue[] {
  if (
    item.ownership.locked === expected.locked &&
    item.ownership.modelWritable === expected.modelWritable
  ) {
    return [];
  }
  return [
    {
      code: "ownership_boundary_violation",
      path,
      message: `${item.id} has an invalid locked/modelWritable boundary`,
    },
  ];
}

function overlaps(left: Bounds, right: Bounds): boolean {
  return (
    left.x < right.x + right.width &&
    left.x + left.width > right.x &&
    left.y < right.y + right.height &&
    left.y + left.height > right.y
  );
}

export function validateDeckMasterContract(
  contract: DeckMasterContractV1,
): MasterValidation {
  const errors: ValidationIssue[] = [];
  if (contract.contractVersion !== "deckmaster-contract/v1") {
    errors.push({
      code: "contract_version",
      path: "contractVersion",
      message: "DeckMasterContract v1 is required",
    });
  }
  for (const item of Object.values(contract.furniture)) {
    errors.push(
      ...ownershipIssues(
        item,
        { locked: true, modelWritable: false },
        `furniture.${item.id}.ownership`,
      ),
    );
  }

  const furniture = Object.values(contract.furniture);
  const furnitureIds = new Set(furniture.map((item) => item.id));
  const slotIds = new Set<string>();
  let furnitureBodyDuplicates = 0;
  let writableOverlaps = 0;

  for (const kind of REQUIRED_LAYOUT_KINDS) {
    const layout = contract.layouts[kind];
    if (!layout) {
      errors.push({
        code: "missing_layout",
        path: "layouts",
        message: `Missing ${kind} layout`,
      });
      continue;
    }
    if (layout.kind !== kind) {
      errors.push({
        code: "layout_kind_mismatch",
        path: `layouts.${kind}.kind`,
        message: `${kind} layout has an invalid kind`,
      });
    }

    const requiredSlotRoles = new Set<MasterSlot["role"]>(["title", "body"]);
    const placeholderIndexes = new Set<number>();
    for (const slot of layout.slots) {
      requiredSlotRoles.delete(slot.role);
      errors.push(
        ...ownershipIssues(
          slot,
          { locked: false, modelWritable: true },
          `layouts.${layout.id}.${slot.id}.ownership`,
        ),
      );
      if (placeholderIndexes.has(slot.placeholderIndex)) {
        errors.push({
          code: "duplicate_placeholder_index",
          path: `layouts.${layout.id}.${slot.id}.placeholderIndex`,
          message: `${layout.id} reuses placeholder ${slot.placeholderIndex}`,
        });
      }
      placeholderIndexes.add(slot.placeholderIndex);
      if (furnitureIds.has(slot.id)) furnitureBodyDuplicates += 1;
      if (slotIds.has(slot.id)) furnitureBodyDuplicates += 1;
      slotIds.add(slot.id);
      for (const item of furniture) {
        if (overlaps(slot.bounds, item.bounds)) writableOverlaps += 1;
      }
    }
    for (const role of requiredSlotRoles) {
      errors.push({
        code: "missing_editable_placeholder",
        path: `layouts.${layout.id}.slots`,
        message: `${layout.id} is missing an editable ${role} placeholder`,
      });
    }
  }

  if (furnitureBodyDuplicates) {
    errors.push({
      code: "master_body_namespace_overlap",
      path: "layouts",
      message: "Master furniture and writable slots overlap",
    });
  }
  if (writableOverlaps) {
    errors.push({
      code: "master_body_geometry_overlap",
      path: "layouts",
      message: "Writable slots overlap locked master furniture",
    });
  }
  return {
    valid: errors.length === 0,
    errors,
    furnitureBodyDuplicates,
    writableOverlaps,
    titleFitFailures: 0,
  };
}
