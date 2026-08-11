import type {
  RenderAssetDependency,
  RenderChartSnapshot,
  RenderChartSnapshotManifest,
  RenderFontDependency,
  RenderOverflowValidation,
  RenderPresentationSnapshot,
  RenderRuntimeDependency,
  RenderScriptPolicy,
  RenderSelectionMapEntry,
  RenderSlideDiscovery,
  RenderTarget,
} from "./contracts.ts";

export class RenderPolicyError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "RenderPolicyError";
    this.code = code;
  }
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function visit(value: unknown, visitor: (record: JsonRecord) => void): void {
  if (Array.isArray(value)) {
    value.forEach((item) => visit(item, visitor));
    return;
  }
  if (!isRecord(value)) return;
  visitor(value);
  Object.values(value).forEach((item) => visit(item, visitor));
}

function recordId(record: JsonRecord): string | null {
  for (const key of ["id", "element_id", "object_id", "chart_id"]) {
    if (typeof record[key] === "string" && record[key]) {
      return record[key] as string;
    }
  }
  return null;
}

export function discoverSlides(
  snapshot: RenderPresentationSnapshot,
): RenderSlideDiscovery {
  const slideIds = snapshot.slides.map((slide, index) =>
    typeof slide.id === "string" && slide.id ? slide.id : `slide-${index + 1}`,
  );
  if (new Set(slideIds).size !== slideIds.length) {
    throw new RenderPolicyError("render_duplicate_slide_id");
  }
  return Object.freeze({
    method: "persisted-slide-array",
    count: slideIds.length,
    slide_ids: Object.freeze(slideIds),
  });
}

export function buildSelectionMap(
  snapshot: RenderPresentationSnapshot,
): readonly RenderSelectionMapEntry[] {
  return discoverSlides(snapshot).slide_ids.map((slideId, index) => {
    const objectIds = new Set<string>();
    visit(snapshot.slides[index], (record) => {
      const id = recordId(record);
      if (id && id !== slideId) objectIds.add(id);
    });
    return Object.freeze({
      slide_id: slideId,
      object_ids: Object.freeze([...objectIds]),
    });
  });
}

export function validateOverflow(
  snapshot: RenderPresentationSnapshot,
): RenderOverflowValidation {
  const overflowIds = new Set<string>();
  visit(snapshot.slides, (record) => {
    const overflow =
      record.overflow === true ||
      record.overflow_status === "failed" ||
      record.overflow_status === "hard";
    if (overflow) overflowIds.add(recordId(record) ?? "unidentified-overflow");
  });
  return Object.freeze({
    status: overflowIds.size ? "failed" : "passed",
    overflow_object_ids: Object.freeze([...overflowIds]),
  });
}

export function chartIds(
  snapshot: RenderPresentationSnapshot,
): readonly string[] {
  const ids = new Set<string>();
  snapshot.slides.forEach((slide, slideIndex) => {
    visit(slide, (record) => {
      if (record.type === "chart" || record.kind === "chart") {
        ids.add(recordId(record) ?? `slide-${slideIndex + 1}-chart-${ids.size + 1}`);
      }
    });
    for (const value of Object.values(slide)) {
      if (
        typeof value === "string" &&
        /(?:data-(?:smart-)?chart|<canvas\b|class=["'][^"']*\bchart\b)/i.test(value)
      ) {
        ids.add(`slide-${slideIndex + 1}-html-chart`);
      }
    }
  });
  return Object.freeze([...ids]);
}

export function chartSnapshotManifest(
  snapshot: RenderPresentationSnapshot,
  target: RenderTarget,
  snapshots: readonly RenderChartSnapshot[] = [],
): RenderChartSnapshotManifest {
  const ids = chartIds(snapshot);
  if (!ids.length) {
    return Object.freeze({
      status: "not-required",
      chart_ids: ids,
      snapshots: Object.freeze([]),
    });
  }
  const knownSlideIds = new Set(discoverSlides(snapshot).slide_ids);
  const seenSnapshotIds = new Set<string>();
  for (const item of snapshots) {
    if (
      !ids.includes(item.chart_id) ||
      seenSnapshotIds.has(item.chart_id) ||
      !knownSlideIds.has(item.slide_id) ||
      !item.asset_id ||
      !/^[0-9a-f]{64}$/i.test(item.sha256) ||
      (item.mime_type !== "image/png" && item.mime_type !== "image/svg+xml")
    ) {
      throw new RenderPolicyError("render_chart_snapshot_invalid");
    }
    seenSnapshotIds.add(item.chart_id);
  }
  const snapshotIds = new Set(snapshots.map((item) => item.chart_id));
  const missing = ids.filter((id) => !snapshotIds.has(id));
  if ((target === "pdf" || target === "pptx" || target === "thumbnail") && missing.length) {
    throw new RenderPolicyError(`render_chart_snapshot_required:${missing.join(",")}`);
  }
  return Object.freeze({
    status: missing.length ? "runtime-required" : "complete",
    chart_ids: ids,
    snapshots: Object.freeze([...snapshots]),
  });
}

function containsUnsafeMarkup(snapshot: RenderPresentationSnapshot, pattern: RegExp): boolean {
  let found = false;
  visit(snapshot.slides, (record) => {
    if (found) return;
    found = Object.values(record).some(
      (value) => typeof value === "string" && pattern.test(value),
    );
  });
  return found;
}

export function scriptPolicy(
  snapshot: RenderPresentationSnapshot,
  target: RenderTarget,
): RenderScriptPolicy {
  const exportTarget = target === "pdf" || target === "pptx" || target === "thumbnail";
  if (exportTarget && containsUnsafeMarkup(snapshot, /<iframe\b/i)) {
    throw new RenderPolicyError("render_iframe_forbidden");
  }
  if (containsUnsafeMarkup(snapshot, /<script\b|javascript\s*:/i)) {
    throw new RenderPolicyError("render_script_source_forbidden");
  }
  const controlledRuntime =
    !exportTarget && snapshot.profile.content_model === "smart-html";
  return Object.freeze({
    mode: controlledRuntime ? "controlled-runtime" : "deny",
    allow_inline: false,
    allow_eval: false,
    allow_external: false,
    iframe_free: exportTarget,
  });
}

export function validateDependencies(
  fonts: readonly RenderFontDependency[] = [],
  assets: readonly RenderAssetDependency[] = [],
  snapshot?: RenderPresentationSnapshot,
): void {
  const fontFamilies = new Set<string>();
  for (const font of fonts) {
    if (
      !font.family.trim() ||
      font.status !== "ready" ||
      fontFamilies.has(font.family) ||
      (font.sha256 !== undefined && !/^[0-9a-f]{64}$/i.test(font.sha256))
    ) {
      throw new RenderPolicyError("render_font_not_ready");
    }
    fontFamilies.add(font.family);
    if (font.asset_id && !font.sha256) {
      throw new RenderPolicyError("render_dependency_digest_required");
    }
  }
  const assetIds = new Set<string>();
  for (const asset of assets) {
    if (
      !asset.asset_id.trim() ||
      asset.status !== "ready" ||
      assetIds.has(asset.asset_id) ||
      (asset.sha256 !== undefined && !/^[0-9a-f]{64}$/i.test(asset.sha256))
    ) {
      throw new RenderPolicyError("render_asset_not_ready");
    }
    assetIds.add(asset.asset_id);
    if (!asset.sha256) {
      throw new RenderPolicyError("render_dependency_digest_required");
    }
  }
  if (!snapshot) return;
  const requiredAssetIds = new Set<string>();
  visit(snapshot.slides, (record) => {
    const assetId = record.asset_id ?? record.assetId;
    if (typeof assetId === "string" && assetId.trim()) {
      requiredAssetIds.add(assetId);
    }
  });
  for (const assetId of requiredAssetIds) {
    if (!assetIds.has(assetId)) {
      throw new RenderPolicyError(`render_asset_dependency_required:${assetId}`);
    }
  }
}

export function validateRuntimeDependencies(
  dependencies: readonly RenderRuntimeDependency[],
): void {
  if (!dependencies.length) {
    throw new RenderPolicyError("render_runtime_dependency_required");
  }
  const ids = new Set<string>();
  for (const dependency of dependencies) {
    if (
      !dependency.id.trim() ||
      !dependency.version.trim() ||
      ids.has(dependency.id) ||
      (dependency.integrity !== undefined &&
        !/^(?:sha256-)?[0-9a-f]{64}$/i.test(dependency.integrity))
    ) {
      throw new RenderPolicyError("render_runtime_dependency_invalid");
    }
    ids.add(dependency.id);
  }
}
