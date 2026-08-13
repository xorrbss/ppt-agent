import type {
  AuthoredOcrStatus,
  AuthoredQualityEvidenceAtom,
  AuthoredQualityEvidenceState,
  AuthoredQualityIncompleteMeasurement,
  AuthoredQualityReviewElementChange,
  AuthoredQualityReviewMetrics,
  AuthoredQualityReviewOcrCheck,
  AuthoredQualityReviewOcrComparison,
  AuthoredQualityReviewOcrOmissions,
  AuthoredQualityReviewPercentiles,
  AuthoredQualityReviewResult,
  AuthoredQualityReviewSlideResult,
  AuthoredQualityReviewStageUsage,
  AuthoredReviewRepairPath,
} from "./authored";

import { deriveResolvedOmissions, firstDefined, isRecord, normalizeApplied, normalizeBoolean, normalizeIndexList, normalizeIssueList, normalizeNumber, normalizeOcrCheck, normalizeOcrOmissions, normalizeOcrStatus, normalizeOptionalString, normalizeRepairPath, normalizeStringList, normalizeUnattributedIssues } from "./authored-quality-normalize-base";

export function normalizeDisplayValue(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  return undefined;
}

export function normalizeEvidenceState(value: unknown): AuthoredQualityEvidenceState | null {
  switch (value) {
    case "proven_clean":
    case "proven_issue":
    case "unknown":
    case "not_applicable":
      return value;
    default:
      return null;
  }
}

export function normalizeEvidenceAtoms(value: unknown): AuthoredQualityEvidenceAtom[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!isRecord(item)) return [];
    const state = normalizeEvidenceState(item.state);
    if (!state) return [];
    const atom: AuthoredQualityEvidenceAtom = { state };
    for (const key of ["qid", "constraint_id", "metric", "stage", "reason"] as const) {
      const normalized = normalizeOptionalString(
        key === "reason" ? firstDefined(item.reason, item.reason_code) : item[key],
      );
      if (normalized) atom[key] = normalized;
    }
    return [atom];
  });
}

export function normalizeIncompleteMeasurements(
  value: unknown,
): AuthoredQualityIncompleteMeasurement[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.flatMap((item) => {
    if (!isRecord(item)) return [];
    const reason = normalizeOptionalString(
      firstDefined(item.reason, item.reason_code, item.code, item.message),
    );
    if (!reason) return [];
    const measurement: AuthoredQualityIncompleteMeasurement = { reason };
    for (const key of ["qid", "constraint_id", "metric", "stage"] as const) {
      const normalized = normalizeOptionalString(item[key]);
      if (normalized) measurement[key] = normalized;
    }
    const identity = [
      measurement.qid,
      measurement.constraint_id,
      measurement.metric,
      measurement.stage,
      measurement.reason,
    ].join("\\u0000");
    if (seen.has(identity)) return [];
    seen.add(identity);
    return [measurement];
  });
}

export function normalizeRepairBlockers(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const blockers: string[] = [];
  const seen = new Set<string>();
  const append = (parts: Array<string | null | undefined>) => {
    const label = parts.filter(Boolean).join(" · ");
    if (!label || seen.has(label)) return;
    seen.add(label);
    blockers.push(label);
  };
  for (const item of value) {
    if (!isRecord(item)) continue;
    const element = isRecord(item.element) ? item.element : {};
    const detector = isRecord(item.detector_evidence)
      ? item.detector_evidence
      : {};
    const base = [
      normalizeOptionalString(
        firstDefined(item.reason_code, item.reason, item.code, item.message),
      ),
      normalizeDisplayValue(firstDefined(element.qid, item.qid))
        ? `qid=${normalizeDisplayValue(firstDefined(element.qid, item.qid))}`
        : undefined,
      normalizeDisplayValue(item.constraint_id)
        ? `constraint=${normalizeDisplayValue(item.constraint_id)}`
        : undefined,
      normalizeDisplayValue(item.operation)
        ? `operation=${normalizeDisplayValue(item.operation)}`
        : undefined,
      normalizeDisplayValue(item.stage)
        ? `stage=${normalizeDisplayValue(item.stage)}`
        : undefined,
      normalizeDisplayValue(item.metric)
        ? `metric=${normalizeDisplayValue(item.metric)}`
        : undefined,
    ];
    const physicalOwners = Array.isArray(detector.physicalOwnerBlockers)
      ? detector.physicalOwnerBlockers
      : [];
    if (physicalOwners.length === 0) {
      append(base);
      continue;
    }
    for (const rawOwner of physicalOwners) {
      if (!isRecord(rawOwner)) continue;
      append([
        ...base,
        normalizeDisplayValue(rawOwner.ownerQid)
          ? `owner=${normalizeDisplayValue(rawOwner.ownerQid)}`
          : undefined,
        normalizeDisplayValue(rawOwner.requiredDownPx)
          ? `requiredDownPx=${normalizeDisplayValue(rawOwner.requiredDownPx)}`
          : undefined,
        normalizeDisplayValue(rawOwner.availableDownPx)
          ? `availableDownPx=${normalizeDisplayValue(rawOwner.availableDownPx)}`
          : undefined,
        normalizeDisplayValue(rawOwner.lowerBoundaryQid)
          ? `lowerBoundary=${normalizeDisplayValue(rawOwner.lowerBoundaryQid)}`
          : undefined,
        normalizeDisplayValue(rawOwner.lowerBoundaryYPx)
          ? `lowerBoundaryYPx=${normalizeDisplayValue(rawOwner.lowerBoundaryYPx)}`
          : undefined,
        normalizeDisplayValue(rawOwner.candidateContained)
          ? `candidateContained=${normalizeDisplayValue(rawOwner.candidateContained)}`
          : undefined,
      ]);
    }
  }
  return blockers;
}

export function normalizeElementChange(
  value: unknown,
  legacyApplied: boolean,
): AuthoredQualityReviewElementChange | null {
  if (!isRecord(value)) return null;
  const path = normalizeRepairPath(value.path);
  const change: AuthoredQualityReviewElementChange = {
    applied: normalizeApplied(value.applied, legacyApplied),
    resolved_issues: normalizeIssueList(value.resolved_issues),
    remaining_issues: normalizeIssueList(value.remaining_issues),
    path,
    changed_properties: [],
  };
  const elementId = value.element_id ?? value.elementId ?? value.qid;
  const before = normalizeDisplayValue(value.before);
  const after = normalizeDisplayValue(value.after);
  const changedProperties = normalizeStringList(
    firstDefined(value.changed_properties, value.changedProperties, value.properties),
  );
  if (typeof elementId === "string" && elementId.trim()) {
    change.element_id = elementId.trim();
  }
  if (before !== undefined) change.before = before;
  if (after !== undefined) change.after = after;
  change.changed_properties = changedProperties;
  return change;
}

export function normalizeSlideResult(
  value: unknown,
  fixedSlideIndices: ReadonlySet<number>,
  ocr?: AuthoredQualityReviewOcrCheck,
): AuthoredQualityReviewSlideResult | null {
  if (!isRecord(value)) {
    return null;
  }
  const slideIndex = value.slide_index;
  if (
    typeof slideIndex !== "number" ||
    !Number.isInteger(slideIndex) ||
    slideIndex < 0
  ) {
    return null;
  }
  const path = normalizeRepairPath(value.path);
  const rawChanges = Array.isArray(value.element_changes)
    ? value.element_changes
    : Array.isArray(value.changes)
      ? value.changes
      : [];
  const rejectionReason = normalizeOptionalString(value.rejection_reason);
  const correctionReason = normalizeOptionalString(
    value.correction_reason ?? value.false_positive_reason,
  );
  const candidateMetrics = isRecord(value.metrics) && isRecord(value.metrics.candidates)
    ? value.metrics.candidates
    : {};
  const rawOutcomes = Array.isArray(value.outcomes) ? value.outcomes : [];
  const candidateCount = normalizeNumber(
    firstDefined(
      value.candidate_count,
      value.candidate_proposed,
      candidateMetrics.proposed,
    ),
  ) ?? rawOutcomes.filter((outcome) => isRecord(outcome) && outcome.proposal_status === "proposed").length;
  const candidateRejectionReasons = [
    ...rawOutcomes.flatMap((outcome) =>
      isRecord(outcome) && outcome.validation_status === "rejected"
        ? normalizeIssueList(outcome.reason_codes)
        : [],
    ),
  ];
  if (Array.isArray(value.candidate_rejections)) {
    for (const rejection of value.candidate_rejections) {
      if (isRecord(rejection)) candidateRejectionReasons.push(...normalizeIssueList(rejection.reason_codes));
    }
  }
  if (Array.isArray(value.projection_rejections)) {
    for (const rejection of value.projection_rejections) {
      if (!isRecord(rejection)) continue;
      candidateRejectionReasons.push(...normalizeIssueList(rejection.reason_codes));
      const projectionReason = normalizeOptionalString(rejection.reason_code);
      if (projectionReason) candidateRejectionReasons.push(projectionReason);
    }
  }
  const repairBlockers = normalizeRepairBlockers(
    firstDefined(value.projection_rejections, value.repair_blockers),
  );
  // Older payloads did not expose acceptance. Only a fixed-slide index is
  // reliable evidence of a saved change; path alone may describe a rejected
  // candidate.
  const applied = normalizeApplied(
    value.applied,
    fixedSlideIndices.has(slideIndex),
  );
  const attempted =
    typeof value.attempted === "boolean"
      ? value.attempted
      : path !== "none" || rawChanges.length > 0 || rejectionReason !== null;
  const accepted = value.accepted === true;
  const changes = rawChanges.flatMap((change) => {
    const normalized = normalizeElementChange(change, applied);
    return normalized ? [normalized] : [];
  });
  const evidenceAtoms = normalizeEvidenceAtoms(
    firstDefined(value.evidence_atoms, value.evidenceAtoms, value.dom_evidence_atoms),
  );
  const incompleteMeasurements = normalizeIncompleteMeasurements(
    firstDefined(
      value.incomplete_measurements,
      value.incompleteMeasurements,
      value.measurement_gaps,
    ),
  );
  const publishBlockers = normalizeIncompleteMeasurements(
    firstDefined(value.publish_blockers, value.publishBlockers),
  );
  const confirmedIssueCount = normalizeNumber(value.confirmed_issue_count) ??
    evidenceAtoms.filter((atom) => atom.state === "proven_issue").length;
  const unresolvedConfirmedIssueCount = normalizeNumber(
    value.unresolved_confirmed_issue_count,
  ) ?? Math.max(0, confirmedIssueCount - normalizeIssueList(value.resolved_issues).length);
  return {
    slide_index: slideIndex,
    attempted,
    accepted,
    applied,
    apply_status:
      typeof value.apply_status === "string" ? value.apply_status : "not_started",
    publish_status:
      typeof value.publish_status === "string"
        ? value.publish_status
        : "not_started",
    post_verify_status:
      typeof value.post_verify_status === "string"
        ? value.post_verify_status
        : "not_started",
    rolled_back: value.rolled_back === true,
    ...(rejectionReason === null ? {} : { rejection_reason: rejectionReason }),
    candidate_count: candidateCount,
    candidate_rejection_reasons: [...new Set(candidateRejectionReasons)],
    repair_blockers: repairBlockers,
    corrected_false_positive:
      value.corrected_false_positive === true ||
      value.detector_disposition === "false_positive",
    ...(correctionReason === null ? {} : { correction_reason: correctionReason }),
    detected_issues: normalizeIssueList(
      value.detected_issues ?? value.detected ?? value.issues_before,
    ),
    evidence_atoms: evidenceAtoms,
    incomplete_measurements: incompleteMeasurements,
    publish_blockers: publishBlockers,
    confirmed_issue_count: confirmedIssueCount,
    unresolved_confirmed_issue_count: unresolvedConfirmedIssueCount,
    element_changes: changes,
    resolved_issues: normalizeIssueList(value.resolved_issues),
    remaining_issues: normalizeIssueList(
      value.remaining_issues ?? value.issues,
    ),
    path,
    ...(ocr ? { ocr } : {}),
  };
}
