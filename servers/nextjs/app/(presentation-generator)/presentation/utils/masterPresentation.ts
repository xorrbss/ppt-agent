import { recordOrNull } from "@/lib/guards";

type UnknownRecord = Record<string, unknown>;

export interface PresentationMasterPin {
  masterId: string;
  revision: number;
  digest: string;
  name?: string;
  layoutRegistryDigest?: string;
  fontManifestDigest?: string;
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
  const source = recordOrNull(presentation);
  if (!source) return null;
  const authoredMetadata =
    recordOrNull(recordOrNull(source.theme)?.authored_vnext) ??
    recordOrNull(recordOrNull(source.profile)?.authored_vnext);
  const pin =
    recordOrNull(source.masterPin) ??
    recordOrNull(source.master_pin) ??
    recordOrNull(authoredMetadata?.masterPin) ??
    recordOrNull(authoredMetadata?.master_pin);
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

export function presentationSlideLayoutId(
  presentation: unknown,
  slideIndex: number | undefined,
): string | null {
  if (!Number.isInteger(slideIndex) || Number(slideIndex) < 0) return null;
  const source = recordOrNull(presentation);
  const slides = Array.isArray(source?.slides) ? source.slides : [];
  const slide = recordOrNull(slides[Number(slideIndex)]);
  if (!slide) return null;
  const content = recordOrNull(slide.content);

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
