export type ExternalRelationshipRisk =
  | "HYPERLINK"
  | "HTTP_RESOURCE"
  | "UNC_RESOURCE"
  | "FILE_RESOURCE"
  | "TEMPLATE_LINK"
  | "UNKNOWN_EXTERNAL";

export type ExternalRelationshipDisposition =
  | "WARN_PRESERVE"
  | "QUARANTINE"
  | "REMOVE_APPROVED";

export interface ExternalRelationshipLike {
  readonly source: string;
  readonly relsPart: string;
  readonly id: string;
  readonly type: string;
  readonly target: string;
  readonly targetMode: "Internal" | "External";
}

export interface ExternalRelationshipDecision {
  readonly source: string;
  readonly relsPart: string;
  readonly id: string;
  readonly type: string;
  readonly target: string;
  readonly risk: ExternalRelationshipRisk;
  readonly disposition: ExternalRelationshipDisposition;
  readonly reason: string;
}

export interface ExternalRelationshipPolicyReceipt {
  readonly schemaVersion: "ooxml-external-relationship-policy/v1";
  readonly decisions: readonly ExternalRelationshipDecision[];
  readonly warnings: readonly string[];
  readonly quarantineReasons: readonly string[];
}

function relationshipKey(value: ExternalRelationshipLike): string {
  return `${value.relsPart}#${value.id}`;
}

export function classifyExternalRelationship(
  relationship: ExternalRelationshipLike,
): ExternalRelationshipDecision {
  if (relationship.targetMode !== "External") {
    throw new Error("External relationship policy received an internal relationship");
  }
  const key = relationshipKey(relationship);
  const type = relationship.type.toLowerCase();
  const target = relationship.target.trim();
  let risk: ExternalRelationshipRisk;
  let disposition: ExternalRelationshipDisposition;
  if (/attachedtemplate|templatelink|\/template$/i.test(type)) {
    risk = "TEMPLATE_LINK";
    disposition = "QUARANTINE";
  } else if (/^(?:\\\\|\/\/)/.test(target)) {
    risk = "UNC_RESOURCE";
    disposition = "QUARANTINE";
  } else if (/^file:/i.test(target)) {
    risk = "FILE_RESOURCE";
    disposition = "QUARANTINE";
  } else if (/^mailto:/i.test(target)) {
    // A mail target stays inert until user activation. Preserve it exactly
    // with a warning even when a producer used a non-standard rel type.
    risk = "HYPERLINK";
    disposition = "WARN_PRESERVE";
  } else if (/\/hyperlink$/i.test(type) && /^https?:/i.test(target)) {
    risk = "HYPERLINK";
    disposition = "WARN_PRESERVE";
  } else if (/^https?:/i.test(target)) {
    risk = "HTTP_RESOURCE";
    disposition = "QUARANTINE";
  } else {
    risk = "UNKNOWN_EXTERNAL";
    disposition = "QUARANTINE";
  }
  return Object.freeze({
    source: relationship.source,
    relsPart: relationship.relsPart,
    id: relationship.id,
    type: relationship.type,
    target: relationship.target,
    risk,
    disposition,
    reason: `${disposition.toLowerCase()}:${risk.toLowerCase()}:${key}`,
  });
}

export function evaluateExternalRelationshipPolicy(
  relationships: readonly ExternalRelationshipLike[],
): ExternalRelationshipPolicyReceipt {
  const decisions = relationships
    .filter(({ targetMode }) => targetMode === "External")
    .map(classifyExternalRelationship)
    .sort((left, right) =>
      [left.relsPart, left.id].join("\u001f").localeCompare(
        [right.relsPart, right.id].join("\u001f"),
        "en",
      ),
    );
  return Object.freeze({
    schemaVersion: "ooxml-external-relationship-policy/v1" as const,
    decisions: Object.freeze(decisions),
    warnings: Object.freeze(
      decisions
        .filter(({ disposition }) => disposition === "WARN_PRESERVE")
        .map(({ reason }) => reason),
    ),
    quarantineReasons: Object.freeze(
      decisions
        .filter(({ disposition }) => disposition === "QUARANTINE")
        .map(({ reason }) => reason),
    ),
  });
}

/**
 * Produces an explicit removal plan. Mutation remains a separate, validated
 * operation because deleting a relationship can also require editing its
 * source XML. Callers must never treat this plan as proof that removal occurred.
 */
export function planExternalRelationshipRemoval(
  receipt: ExternalRelationshipPolicyReceipt,
  includeWarnings = false,
): readonly ExternalRelationshipDecision[] {
  return Object.freeze(
    receipt.decisions
      .filter(({ disposition }) =>
        disposition === "QUARANTINE" ||
        (includeWarnings && disposition === "WARN_PRESERVE"),
      )
      .map((decision) => Object.freeze({ ...decision, disposition: "REMOVE_APPROVED" as const })),
  );
}
