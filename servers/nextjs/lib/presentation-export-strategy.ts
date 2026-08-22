import { isAuthoredPresentation } from "./authored-hybrid/presentation-mode.ts";
import type { PptxMode } from "./authored-hybrid/mode.ts";
import type { AuthoredHybridExportMetadata } from "./authored-hybrid/export-metadata.ts";
import type { PresentationExportQualityReport } from "./presentation-export-quality.ts";
import {
  resolveServerIssuedPresentationProfile,
} from "./presentation-profile.ts";
import { assertRenderArtifactFresh } from "./render-service/freshness.ts";
import type {
  RenderArtifact,
  RenderSourceIdentity,
} from "./render-service/contracts.ts";

export type PersistedExportStrategy =
  | "template-v2-general"
  | "authored-hybrid";

export const PRESENTATION_SOURCE_SHA256 = Symbol(
  "presenton.presentationSourceSha256"
);

export interface PersistedPresentation {
  id?: unknown;
  version?: unknown;
  mode?: unknown;
  theme?: unknown;
  slides?: unknown;
  revision?: unknown;
  lifecycle_status?: unknown;
  profile?: unknown;
  capabilities?: unknown;
  [PRESENTATION_SOURCE_SHA256]?: string;
}

export interface PresentationExportExecutionParams {
  format: "pdf" | "pptx";
  presentationId: string;
  title?: string;
  cookieHeader?: string;
  pptxMode?: PptxMode;
  /** Hybrid requests embed by default; only explicit false selects compatibility mode. */
  fontEmbedding?: boolean;
  /** Durable FastAPI attempt supplied by the caller or created by the Next route. */
  exportAttemptId?: string;
  /** Canonical FastAPI snapshot digest bound to `exportAttemptId`. */
  canonicalSourceDigest?: string;
  expectedPresentationSha256?: string;
  /** Server-bound optimistic revision; client hints never select an adapter. */
  expectedPresentationRevision?: number;
  expectedArtifactVersion?: string;
  /**
   * Correlation id for progress reporting only. It selects no adapter and
   * gates nothing, so an absent or unknown value costs the caller narration,
   * never the export.
   */
  operationId?: string;
}

export interface PresentationExportResult {
  path: string;
  /** Optional additive metadata; legacy exporters and callers may omit it. */
  quality?: PresentationExportQualityReport;
  /** Stable aggregate summary for authored-hybrid API consumers. */
  exportMetadata?: AuthoredHybridExportMetadata;
  /** Canonical additive result. Path-only runners remain a tested rolling facade. */
  renderArtifact?: RenderArtifact;
}

export const PATH_ONLY_EXPORT_COMPATIBILITY = Object.freeze({
  source_of_truth: "renderArtifact" as const,
  removal_condition:
    "Remove path-only results after every supported exporter returns a revision- and digest-bound presenton.render-artifact/v1 and rollback from the first enforcing release has been tested.",
});

export interface PresentationExportAdapterRegistry<
  Result extends PresentationExportResult = PresentationExportResult,
> {
  general(
    params: PresentationExportExecutionParams
  ): Promise<Result>;
  authoredFidelity?(
    params: PresentationExportExecutionParams
  ): Promise<Result>;
  hybrid(
    params: PresentationExportExecutionParams
  ): Promise<Result>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readThemeMode(theme: unknown): unknown {
  return isRecord(theme) ? theme.mode : undefined;
}

/**
 * Resolve export solely from the persisted identity and payload invariant.
 * Client mode hints are interpreted only after this identity gate succeeds.
 */
export function resolvePersistedExportStrategy(
  presentation: PersistedPresentation
): PersistedExportStrategy {
  const version = presentation.version ?? "v1-standard";
  const mode = presentation.mode ?? null;
  if (version !== "v1-standard" && version !== "v2-standard") {
    throw new Error("unsupported_presentation_identity");
  }
  if (
    mode !== null &&
    mode !== "template" &&
    mode !== "authored"
  ) {
    throw new Error("unsupported_presentation_identity");
  }

  const slides = presentation.slides;
  if (!Array.isArray(slides) || slides.length === 0) {
    throw new Error("presentation_slides_required");
  }

  const themeMode = readThemeMode(presentation.theme);
  if (
    themeMode !== undefined &&
    themeMode !== "template" &&
    themeMode !== "authored"
  ) {
    throw new Error("unsupported_presentation_identity");
  }
  if (
    mode !== null &&
    themeMode !== undefined &&
    themeMode !== mode
  ) {
    throw new Error("presentation_identity_conflict");
  }

  const payloads = slides.map((slide) => {
    if (!isRecord(slide)) throw new Error("invalid_slide_payload");
    return {
      ui: slide.ui,
      html: slide.html_content,
    };
  });
  if (
    payloads.some(
      ({ ui, html }) => ui !== null && ui !== undefined && Boolean(html)
    )
  ) {
    throw new Error("mixed_slide_payload_forbidden");
  }

  if (version === "v2-standard") {
    if (
      (mode !== null && mode !== "template") ||
      (themeMode !== undefined && themeMode !== "template")
    ) {
      throw new Error("template_v2_identity_mismatch");
    }
    if (payloads.some(({ html }) => Boolean(html))) {
      throw new Error("template_v2_authored_payload_forbidden");
    }
    if (payloads.some(({ ui }) => ui === null || ui === undefined)) {
      throw new Error("template_v2_ui_payload_required");
    }
    return validateCanonicalExportStrategy(
      presentation,
      "template-v2-general",
    );
  }

  const authored = isAuthoredPresentation(presentation);
  if (authored) {
    if (themeMode !== undefined && themeMode !== "authored") {
      throw new Error("presentation_identity_conflict");
    }
    if (payloads.some(({ ui }) => ui !== null && ui !== undefined)) {
      throw new Error("authored_identity_payload_mismatch");
    }
    if (payloads.some(({ html }) => !html)) {
      throw new Error("authored_html_payload_required");
    }
    return validateCanonicalExportStrategy(presentation, "authored-hybrid");
  }

  throw new Error("unsupported_presentation_identity");
}

function validateCanonicalExportStrategy(
  presentation: PersistedPresentation,
  identityStrategy: PersistedExportStrategy,
): PersistedExportStrategy {
  const runtime = resolveServerIssuedPresentationProfile(presentation);
  const expected: PersistedExportStrategy =
    runtime.driver.driver_id === "template-v2"
      ? "template-v2-general"
      : "authored-hybrid";
  if (expected !== identityStrategy) {
    throw new Error("presentation_profile_export_identity_conflict");
  }
  if (runtime.capabilities.export_adapter_id !== runtime.driver.export_adapter_id) {
    throw new Error("presentation_profile_export_capability_conflict");
  }
  return expected;
}

function renderSourceIdentity(
  presentation: PersistedPresentation,
): RenderSourceIdentity | null {
  if (
    typeof presentation.id !== "string" ||
    !Number.isSafeInteger(presentation.revision) ||
    (presentation.lifecycle_status !== "published" &&
      presentation.lifecycle_status !== "staging")
  ) {
    return null;
  }
  return {
    presentation_id: presentation.id,
    revision: Number(presentation.revision),
    lifecycle_status: presentation.lifecycle_status,
    published: presentation.lifecycle_status === "published",
    ...(presentation[PRESENTATION_SOURCE_SHA256]
      ? { source_sha256: presentation[PRESENTATION_SOURCE_SHA256] }
      : {}),
  };
}

function validateCanonicalExportResult<Result extends PresentationExportResult>(
  presentation: PersistedPresentation,
  params: PresentationExportExecutionParams,
  result: Result,
): Result {
  const artifact = result.renderArtifact;
  if (!artifact) return result;
  const source = renderSourceIdentity(presentation);
  if (!source) throw new Error("render_artifact_source_identity_required");
  assertRenderArtifactFresh(artifact, source);
  if (artifact.target !== params.format) {
    throw new Error("render_artifact_target_mismatch");
  }
  if (
    params.expectedArtifactVersion !== undefined &&
    params.expectedArtifactVersion !== artifact.artifact_version
  ) {
    throw new Error("stale_render_artifact_version");
  }
  return result;
}

export async function executePersistedPresentationExport<
  Result extends PresentationExportResult,
>(
  presentation: PersistedPresentation,
  params: PresentationExportExecutionParams,
  registry: PresentationExportAdapterRegistry<Result>
): Promise<Result> {
  const strategy = resolvePersistedExportStrategy(presentation);
  if (
    params.expectedPresentationRevision !== undefined &&
    params.expectedPresentationRevision !== presentation.revision
  ) {
    throw new Error("stale_render_revision");
  }
  if (
    params.fontEmbedding === true &&
    !(
      params.format === "pptx" &&
      params.pptxMode === "hybrid" &&
      strategy === "authored-hybrid"
    )
  ) {
    throw new Error("font_embedding_requires_authored_hybrid_export");
  }
  if (
    params.format === "pptx" &&
    params.pptxMode === "hybrid" &&
    strategy === "authored-hybrid"
  ) {
    const hybridParams = {
      ...params,
      fontEmbedding: params.fontEmbedding !== false,
    };
    return validateCanonicalExportResult(
      presentation,
      hybridParams,
      await registry.hybrid(hybridParams),
    );
  }
  if (
    params.format === "pptx" &&
    params.pptxMode !== "hybrid" &&
    strategy === "authored-hybrid" &&
    registry.authoredFidelity
  ) {
    return validateCanonicalExportResult(
      presentation,
      params,
      await registry.authoredFidelity(params),
    );
  }
  return validateCanonicalExportResult(
    presentation,
    params,
    await registry.general(params),
  );
}
