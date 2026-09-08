type UnknownRecord = Record<string, unknown>;

export interface PresentationMasterPin {
  masterId: string;
  revision: number;
  digest: string;
  name?: string;
  layoutRegistryDigest?: string;
  fontManifestDigest?: string;
}

export interface EditableHybridWorkbenchProjection {
  deckId: string;
  status: "shadow" | "ready" | "blocked";
  reasonCode?: string;
}

function record(value: unknown): UnknownRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function firstString(source: UnknownRecord, keys: readonly string[]): string | undefined {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function firstPositiveInteger(
  source: UnknownRecord,
  keys: readonly string[],
): number | undefined {
  for (const key of keys) {
    const value = source[key];
    if (Number.isSafeInteger(value) && Number(value) > 0) return Number(value);
  }
  return undefined;
}

/** Reject partial pins so the editor never presents an ambiguous master as applied. */
export function presentationMasterPin(
  presentation: unknown,
): PresentationMasterPin | null {
  const source = record(presentation);
  if (!source) return null;
  const authoredMetadata =
    record(record(source.theme)?.authored_vnext) ??
    record(record(source.profile)?.authored_vnext);
  const pin =
    record(source.masterPin) ??
    record(source.master_pin) ??
    record(authoredMetadata?.masterPin) ??
    record(authoredMetadata?.master_pin);
  if (!pin) return null;

  const masterId = firstString(pin, ["masterId", "master_id"]);
  const revision = firstPositiveInteger(pin, [
    "revision",
    "masterRevision",
    "master_revision",
  ]);
  const digest = firstString(pin, [
    "digest",
    "masterDigest",
    "master_digest",
  ]);
  if (!masterId || !revision || !digest) return null;

  return {
    masterId,
    revision,
    digest,
    name: firstString(pin, ["name", "displayName", "display_name"]),
    layoutRegistryDigest: firstString(pin, [
      "layoutRegistryDigest",
      "layout_registry_digest",
    ]),
    fontManifestDigest: firstString(pin, [
      "fontManifestDigest",
      "font_manifest_digest",
    ]),
  };
}

/**
 * Accept only the exact normal-publication bridge. A loose theme object must
 * never manufacture a Workbench route or imply editable-Hybrid readiness.
 */
export function presentationEditableHybridWorkbench(
  presentation: unknown,
): EditableHybridWorkbenchProjection | null {
  const source = record(presentation);
  const bridge = record(record(source?.theme)?.authored_vnext);
  if (
    !bridge ||
    bridge.schemaVersion !== "authored-editable-hybrid-scene-projection/v1" ||
    bridge.profileId !== "authored-editable-hybrid-ko-en-basic-v1" ||
    typeof bridge.deckId !== "string" ||
    !/^deck:[a-z0-9][a-z0-9._-]*$/u.test(bridge.deckId) ||
    (bridge.status !== "shadow" && bridge.status !== "ready" && bridge.status !== "blocked")
  ) return null;
  const reasonCode = typeof bridge.reasonCode === "string" && bridge.reasonCode.trim()
    ? bridge.reasonCode.trim()
    : undefined;
  return { deckId: bridge.deckId, status: bridge.status, ...(reasonCode ? { reasonCode } : {}) };
}

export function presentationSlideLayoutId(
  presentation: unknown,
  slideIndex: number | undefined,
): string | null {
  if (!Number.isInteger(slideIndex) || Number(slideIndex) < 0) return null;
  const source = record(presentation);
  const slides = Array.isArray(source?.slides) ? source.slides : [];
  const slide = record(slides[Number(slideIndex)]);
  if (!slide) return null;
  const content = record(slide.content);

  // Authored publication stores the immutable master layout in slide.content.
  // The top-level `layout` value identifies the editor renderer
  // (`authored:<layout-id>`) and must not be sent to the body-only CAS endpoint.
  return (
    (content
      ? firstString(content, [
          "layoutId",
          "layout_id",
          "masterLayout",
          "master_layout",
        ])
      : undefined) ??
    firstString(slide, [
      "layoutId",
      "layout_id",
      "masterLayout",
      "master_layout",
    ]) ?? null
  );
}

export function friendlyMasterName(pin: PresentationMasterPin): string {
  return pin.name ?? "회사 표준";
}

export function friendlyLayoutName(layoutId: string | null): string | null {
  if (!layoutId) return null;
  const normalized = layoutId.split(":").at(-1)?.trim().toLowerCase();
  if (!normalized) return null;
  if (normalized === "cover") return "Cover";
  if (normalized === "section") return "Section";
  if (normalized === "standard") return "Standard";
  if (normalized === "long-title" || normalized === "longtitle") {
    return "Long title";
  }
  return normalized
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** Workspace commands use snake_case and are persisted as durable JSON. */
export function masterPinCommandContext(
  pin: PresentationMasterPin | null,
): Record<string, unknown> {
  if (!pin) return {};
  return {
    master_pin: {
      master_id: pin.masterId,
      revision: pin.revision,
      digest: pin.digest,
      ...(pin.layoutRegistryDigest
        ? { layout_registry_digest: pin.layoutRegistryDigest }
        : {}),
      ...(pin.fontManifestDigest
        ? { font_manifest_digest: pin.fontManifestDigest }
        : {}),
    },
  };
}

/** A pinned slide may change its body only; its document master and layout are fences. */
export function masterPinnedBodyOnlyContext(
  pin: PresentationMasterPin,
  layoutId: string | null,
): Record<string, unknown> | null {
  if (!layoutId) return null;
  return {
    body_only: true,
    allow_layout_change: false,
    ...masterPinCommandContext(pin),
    layout_id: layoutId,
  };
}

/**
 * Why the editable Hybrid export would be refused for this deck, if it would.
 *
 * The capability projection answers whether the *renderer* admits Hybrid; this
 * answers whether the deck's own quality receipt does. `export.pptx` accepts a
 * receipt whose only sealed blocker is a pending visual review for Fidelity
 * (decision `public-export.fidelity-visual-review-pending`, which forbids the
 * same waiver for Hybrid), so a deck generated without the vision-QA pass has
 * a Hybrid button that could only ever fail after the click. Say so instead.
 */
export function presentationHybridQualityBlockReason(
  presentation: unknown,
): string | undefined {
  const gate = record(record(record(presentation)?.theme)?.authoredQualityGate);
  const receipt = record(gate?.qualityReceiptV3);
  // No receipt at all is the retained legacy-Fidelity compatibility path; it is
  // the server's decision to make, not a reason to withhold the option here.
  if (!receipt || receipt.gatePassed === true) return undefined;
  const blockers = Array.isArray(receipt.blockers)
    ? receipt.blockers.filter((entry): entry is string => typeof entry === "string")
    : [];
  // Only a review that never ran is waived, and only for Fidelity. A review
  // that ran and failed refuses every format, so promising Fidelity here would
  // send the user into an export that fails in the timeline.
  if (blockers.length === 1 && blockers[0] === "visualReview.required_failed") {
    return "이 덱은 시각 검수 없이 생성되어 편집 가능한 Hybrid를 사용할 수 없습니다. 다시 만들 때 '고품질 검수 (vision-QA)'를 켜세요. Fidelity PPTX와 PDF는 그대로 내보낼 수 있습니다.";
  }
  if (blockers.includes("visualReview.failed")) {
    return "시각 검수가 통과하지 못해 이 덱은 Hybrid도 Fidelity도 내보낼 수 없습니다. 페이지를 다시 만든 뒤 시도하세요.";
  }
  return `품질 영수증이 통과하지 않아 내보낼 수 없습니다: ${blockers.join(", ") || "사유 미기재"}`;
}
