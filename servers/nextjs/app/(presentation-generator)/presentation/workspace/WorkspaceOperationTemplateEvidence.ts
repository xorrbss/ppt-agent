/**
 * Completion evidence for the `design.apply` receipt rendered by
 * `WorkspaceOperationCard`.
 *
 * Split out of that component to keep it within the repository file-size
 * budget. Pure receipt reading, no React.
 */

import {
  booleanValue,
  numberValue,
  recordValue,
  stringList,
  stringValue,
} from "./WorkspaceOperationResult";

export type TemplateApplicationEvidence = {
  templateId?: string;
  templateDigest?: string;
  referenceLayoutDigest?: string;
  compositionDigest?: string;
  ruleDigest?: string;
  semanticRoles: string[];
  layoutRoles: string[];
  pageNumbers: number[];
  slideCount?: number;
  visualFidelityPassed: boolean;
  previewMatches: boolean;
  nativeMatches: boolean;
  readbackVerified: boolean;
};

function matchesResolvedSnapshot(
  snapshot: Record<string, unknown>,
  evidence: Pick<
    TemplateApplicationEvidence,
    "referenceLayoutDigest" | "compositionDigest" | "ruleDigest" | "slideCount"
  >,
): boolean {
  const referenceLayoutDigest = stringValue(
    snapshot.reference_layout_digest,
    snapshot.referenceLayoutDigest,
  );
  const compositionDigest = stringValue(
    snapshot.composition_digest,
    snapshot.compositionDigest,
  );
  const ruleDigest = stringValue(snapshot.rule_digest, snapshot.ruleDigest);
  const slideCount = numberValue(snapshot.slide_count, snapshot.slideCount);
  return Boolean(
    evidence.referenceLayoutDigest &&
      evidence.compositionDigest &&
      evidence.ruleDigest &&
      evidence.slideCount !== undefined &&
      referenceLayoutDigest === evidence.referenceLayoutDigest &&
      compositionDigest === evidence.compositionDigest &&
      ruleDigest === evidence.ruleDigest &&
      slideCount === evidence.slideCount,
  );
}

export function templateApplicationEvidence(
  result: Record<string, unknown>,
): TemplateApplicationEvidence {
  // The durable design.apply receipt is snake_case.  Keep the older resolved
  // aliases for historical rows, but prefer neither spelling: both describe the
  // same immutable composition and must yield the same completed UI state.
  const templateSnapshot = recordValue(
    result.template_snapshot ?? result.templateSnapshot,
  );
  const referenceLayoutDigest = stringValue(
    result.resolved_reference_layout_digest,
    result.resolvedReferenceLayoutDigest,
    result.reference_layout_digest,
    result.referenceLayoutDigest,
    templateSnapshot.reference_layout_digest,
    templateSnapshot.referenceLayoutDigest,
  );
  const compositionDigest = stringValue(
    result.resolved_composition_digest,
    result.resolvedCompositionDigest,
    result.composition_digest,
    result.compositionDigest,
    recordValue(result.resolved_composition_pin).digest,
    recordValue(result.resolvedCompositionPin).digest,
  );
  const ruleDigest = stringValue(
    result.resolved_rule_digest,
    result.resolvedRuleDigest,
    result.rule_digest,
    result.ruleDigest,
  );
  const slideCount = numberValue(result.slide_count, result.slideCount);
  const pageNumbers = (Array.isArray(result.page_numbers)
    ? result.page_numbers
    : Array.isArray(result.pageNumbers)
      ? result.pageNumbers
      : []
  ).filter(
    (value): value is number => typeof value === "number" && Number.isInteger(value),
  );
  const visualFidelity = recordValue(
    result.visual_fidelity ?? result.visualFidelity,
  );
  const visualFidelityPassed =
    stringValue(visualFidelity.status, result.visual_fidelity_status, result.visualFidelityStatus) ===
      "passed" ||
    booleanValue(visualFidelity.passed, result.visual_fidelity_passed, result.visualFidelityPassed) === true;
  const evidence = {
    templateId: stringValue(
      result.resolved_template_id,
      result.resolvedTemplateId,
      result.template_id,
      result.templateId,
      templateSnapshot.resolved_template_id,
      templateSnapshot.resolvedTemplateId,
      templateSnapshot.template_id,
      templateSnapshot.templateId,
    ),
    templateDigest: stringValue(
      result.resolved_template_digest,
      result.resolvedTemplateDigest,
      result.template_digest,
      result.templateDigest,
      templateSnapshot.resolved_template_digest,
      templateSnapshot.resolvedTemplateDigest,
      templateSnapshot.template_digest,
      templateSnapshot.templateDigest,
    ),
    referenceLayoutDigest,
    compositionDigest,
    ruleDigest,
    semanticRoles: stringList(result.semantic_roles_used, result.semanticRolesUsed),
    layoutRoles: stringList(result.layout_roles_used, result.layoutRolesUsed),
    pageNumbers,
    slideCount,
    visualFidelityPassed,
    previewMatches: false,
    nativeMatches: false,
    readbackVerified:
      booleanValue(result.readback_verified, result.readbackVerified) === true,
  } satisfies TemplateApplicationEvidence;
  return {
    ...evidence,
    previewMatches: matchesResolvedSnapshot(
      recordValue(result.preview_snapshot ?? result.previewSnapshot),
      evidence,
    ),
    nativeMatches: matchesResolvedSnapshot(
      recordValue(result.native_pptx_snapshot ?? result.nativePptxSnapshot),
      evidence,
    ),
  };
}

export function templateApplicationEvidenceComplete(
  evidence: TemplateApplicationEvidence,
): boolean {
  return Boolean(
    evidence.templateId &&
    evidence.templateDigest &&
      evidence.referenceLayoutDigest &&
      evidence.compositionDigest &&
      evidence.ruleDigest &&
      evidence.visualFidelityPassed &&
      evidence.previewMatches &&
      evidence.nativeMatches &&
      evidence.readbackVerified &&
      evidence.slideCount !== undefined &&
      evidence.pageNumbers.length === evidence.slideCount &&
      evidence.pageNumbers.every((pageNumber, index) => pageNumber === index + 1),
  );
}
