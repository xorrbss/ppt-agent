import type {
  DeckMasterContractV1,
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
      message: `${item.id}의 locked/modelWritable 소유권 경계가 올바르지 않습니다.`,
    },
  ];
}

export function validateDeckMasterContract(
  contract: DeckMasterContractV1,
): MasterValidation {
  const errors: ValidationIssue[] = [];
  if (contract.contractVersion !== "deckmaster-contract/v1") {
    errors.push({
      code: "contract_version",
      path: "contractVersion",
      message: "DeckMasterContract v1 계약 버전이 필요합니다.",
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

  for (const kind of REQUIRED_LAYOUT_KINDS) {
    const layout = contract.layouts[kind];
    if (!layout) {
      errors.push({
        code: "missing_layout",
        path: "layouts",
        message: `${kind} 레이아웃이 없습니다.`,
      });
      continue;
    }
    if (layout.kind !== kind) {
      errors.push({
        code: "layout_kind_mismatch",
        path: `layouts.${kind}.kind`,
        message: `${kind} 레이아웃 kind가 올바르지 않습니다.`,
      });
    }

    // `slots` are compatibility-only legacy data.  They neither establish
    // master validity nor contribute to authored-template composition.
  }
  const layoutIds = new Set(Object.values(contract.layouts).map((layout) => layout.id));
  for (const zone of contract.aiEditZones ?? []) {
    const bounds = zone.bounds;
    const hasValidInstruction = zone.instruction === undefined ||
      (typeof zone.instruction === "string" && zone.instruction.length <= 4000);
    const contentTypes = new Set(["auto", "text", "cards", "list", "table", "chart", "mixed"]);
    const patterns = new Set(["auto", "numberedCards", "bulletList", "comparisonTable", "metricChart", "iconCards"]);
    const sourcePolicies = new Set(["documentOnly", "promptAndDocument", "promptOnly"]);
    const emptyBehaviors = new Set(["hidden", "blank", "fallback"]);
    const densities = new Set(["sparse", "balanced", "dense"]);
    const textAlignments = new Set(["left", "center", "right"]);
    const positiveLimit = (value: number | undefined, maximum: number) =>
      value === undefined || (Number.isInteger(value) && value > 0 && value <= maximum);
    if (!/^ai-zone:[a-z0-9][a-z0-9._-]*$/i.test(zone.id) ||
      ![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite) ||
      bounds.width <= 0 || bounds.height <= 0 ||
      (zone.scope.kind === "layout" && !layoutIds.has(zone.scope.layoutId)) ||
      !hasValidInstruction ||
      (zone.contentType !== undefined && !contentTypes.has(zone.contentType)) ||
      (zone.pattern !== undefined && !patterns.has(zone.pattern)) ||
      (zone.sourcePolicy !== undefined && !sourcePolicies.has(zone.sourcePolicy)) ||
      (zone.emptyBehavior !== undefined && !emptyBehaviors.has(zone.emptyBehavior)) ||
      (zone.density !== undefined && !densities.has(zone.density)) ||
      (zone.textAlign !== undefined && !textAlignments.has(zone.textAlign)) ||
      (zone.stylePolicy !== undefined && zone.stylePolicy !== "inheritTemplate") ||
      !positiveLimit(zone.maxChars, 10000) ||
      !positiveLimit(zone.maxItems, 24) ||
      (zone.fallbackText !== undefined && (typeof zone.fallbackText !== "string" || zone.fallbackText.length > 10000)) ||
      (zone.emptyBehavior === "fallback" && !zone.fallbackText?.trim()) ||
      (zone.fallbackText !== undefined && zone.emptyBehavior !== "fallback") ||
      (zone.fallbackText !== undefined && zone.maxChars !== undefined && zone.fallbackText.length > zone.maxChars)) {
      errors.push({
        code: "invalid_ai_edit_zone",
        path: `aiEditZones.${zone.id}`,
        message: `${zone.id}의 도형 또는 AI 합성 제약이 올바르지 않습니다.`,
      });
    }
  }
  return {
    valid: errors.length === 0,
    errors,
    furnitureBodyDuplicates: 0,
    writableOverlaps: 0,
    titleFitFailures: 0,
  };
}
