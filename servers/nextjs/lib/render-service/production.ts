import type {
  RenderArtifact,
  RenderAssetDependency,
  RenderChartSnapshot,
  RenderFontDependency,
  RenderRequest,
  RenderTarget,
} from "./contracts.ts";
import {
  createRenderPresentationSnapshot,
  RenderService,
} from "./render-service.ts";
import { RenderPolicyError } from "./runtime-policy.ts";

type JsonRecord = Record<string, unknown>;

const SHA256 = /^[0-9a-f]{64}$/i;

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

function dependencyDigest(record: JsonRecord): string | undefined {
  const value = record.sha256 ?? record.source_sha256 ?? record.digest;
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string" || !SHA256.test(value)) {
    throw new RenderPolicyError("render_dependency_digest_invalid");
  }
  return value.toLowerCase();
}

function assetKind(value: unknown): RenderAssetDependency["kind"] {
  return value === "font" ||
    value === "document" ||
    value === "template" ||
    value === "generated"
    ? value
    : "image";
}

function persistedFontDependencies(
  presentation: JsonRecord,
): readonly RenderFontDependency[] {
  const fonts = presentation.fonts;
  if (fonts === undefined || fonts === null) return Object.freeze([]);
  if (!isRecord(fonts)) {
    throw new RenderPolicyError("render_font_dependencies_invalid");
  }
  return Object.freeze(
    Object.entries(fonts).map(([family, value]) => {
      if (!family.trim()) {
        throw new RenderPolicyError("render_font_not_ready");
      }
      if (typeof value === "string") {
        if (!value.trim()) {
          throw new RenderPolicyError("render_font_not_ready");
        }
        return Object.freeze({ family, status: "ready" as const });
      }
      if (!isRecord(value)) {
        throw new RenderPolicyError("render_font_dependencies_invalid");
      }
      if (value.status !== "ready") {
        throw new RenderPolicyError("render_font_not_ready");
      }
      const assetId = value.asset_id;
      if (assetId !== undefined && (typeof assetId !== "string" || !assetId.trim())) {
        throw new RenderPolicyError("render_font_not_ready");
      }
      const sha256 = dependencyDigest(value);
      if (typeof assetId === "string" && !sha256) {
        throw new RenderPolicyError("render_dependency_digest_required");
      }
      return Object.freeze({
        family,
        ...(typeof assetId === "string" ? { asset_id: assetId } : {}),
        ...(sha256 ? { sha256 } : {}),
        status: "ready" as const,
      });
    }),
  );
}

function persistedAssetDependencies(
  presentation: JsonRecord,
): readonly RenderAssetDependency[] {
  const dependencies = new Map<string, RenderAssetDependency>();
  visit(presentation.slides, (record) => {
    const assetId = record.asset_id ?? record.assetId;
    if (assetId === undefined || assetId === null) return;
    if (typeof assetId !== "string" || !assetId.trim()) {
      throw new RenderPolicyError("render_asset_not_ready");
    }
    if (record.status !== "ready") {
      throw new RenderPolicyError("render_asset_not_ready");
    }
    const sha256 = dependencyDigest(record);
    if (!sha256) {
      throw new RenderPolicyError("render_dependency_digest_required");
    }
    const candidate = Object.freeze({
      asset_id: assetId,
      kind: assetKind(record.asset_kind ?? record.kind),
      ...(typeof record.mime_type === "string"
        ? { mime_type: record.mime_type }
        : {}),
      ...(sha256 ? { sha256 } : {}),
      status: "ready" as const,
    });
    const previous = dependencies.get(assetId);
    if (
      previous &&
      (previous.sha256 !== candidate.sha256 || previous.kind !== candidate.kind)
    ) {
      throw new RenderPolicyError("render_asset_dependency_conflict");
    }
    dependencies.set(assetId, candidate);
  });
  return Object.freeze([...dependencies.values()]);
}

function persistedChartSnapshots(
  presentation: JsonRecord,
): readonly RenderChartSnapshot[] {
  const dependencies = presentation.render_dependencies;
  if (!isRecord(dependencies) || dependencies.chart_snapshots === undefined) {
    return Object.freeze([]);
  }
  if (!Array.isArray(dependencies.chart_snapshots)) {
    throw new RenderPolicyError("render_chart_snapshot_invalid");
  }
  return Object.freeze(
    dependencies.chart_snapshots.map((value) => {
      if (!isRecord(value)) {
        throw new RenderPolicyError("render_chart_snapshot_invalid");
      }
      return Object.freeze({
        chart_id: value.chart_id,
        slide_id: value.slide_id,
        mime_type: value.mime_type,
        sha256: value.sha256,
        asset_id: value.asset_id,
      }) as RenderChartSnapshot;
    }),
  );
}

export interface ProductionRenderArtifactOptions {
  readonly target: Extract<RenderTarget, "preview" | "pdf" | "pptx">;
  readonly source_sha256: string;
  readonly expected_revision?: number;
  readonly expected_artifact_version?: string;
  readonly service?: Pick<RenderService, "render">;
  readonly request?: Omit<
    RenderRequest,
    | "target"
    | "snapshot"
    | "expected_revision"
    | "expected_artifact_version"
    | "fonts"
    | "assets"
    | "chart_snapshots"
  >;
}

/**
 * Create the artifact used by production preview/export callers. Dependencies
 * are projected only from the persisted server response; caller hints cannot
 * select a driver or silently mark an unresolved dependency as ready.
 */
export function createProductionRenderArtifact(
  presentation: unknown,
  options: ProductionRenderArtifactOptions,
): RenderArtifact {
  if (!isRecord(presentation)) {
    throw new RenderPolicyError("render_presentation_required");
  }
  const snapshot = createRenderPresentationSnapshot(presentation, {
    source_sha256: options.source_sha256,
    require_source_sha256: true,
  });
  const service = options.service ?? new RenderService();
  return service.render({
    ...options.request,
    target: options.target,
    snapshot,
    expected_revision:
      options.expected_revision ?? snapshot.source.revision,
    expected_artifact_version: options.expected_artifact_version,
    fonts: persistedFontDependencies(presentation),
    assets: persistedAssetDependencies(presentation),
    chart_snapshots: persistedChartSnapshots(presentation),
  });
}

export function renderArtifactSlides(
  artifact: RenderArtifact,
): readonly Readonly<JsonRecord>[] {
  const payload = artifact.payload;
  if (!isRecord(payload) || !Array.isArray(payload.slides) || !payload.slides.every(isRecord)) {
    throw new RenderPolicyError("render_artifact_slides_required");
  }
  return payload.slides;
}
