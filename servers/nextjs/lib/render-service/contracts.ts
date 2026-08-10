import type {
  PresentationCapabilities,
  PresentationDriverId,
  PresentationProfile,
} from "../presentation-profile.ts";
import type { TemplateRenderPlan } from "../template-v2-ast/index.ts";

export const RENDER_ARTIFACT_CONTRACT =
  "presenton.render-artifact/v1" as const;

export const RENDER_ARTIFACT_COMPATIBILITY = Object.freeze({
  legacy_export_result_field: "path" as const,
  canonical_field: "renderArtifact" as const,
  removal_condition:
    "Remove the path-only export facade after every bundled and remote exporter returns a validated presenton.render-artifact/v1 bound to presentation revision and source digest, and rolling rollback has been exercised.",
});

export type RenderTarget =
  | "preview"
  | "thumbnail"
  | "selection-map"
  | "pdf"
  | "pptx";

export interface RenderSourceIdentity {
  readonly presentation_id: string;
  readonly revision: number;
  readonly lifecycle_status: "published" | "staging";
  readonly published: boolean;
  readonly source_sha256?: string;
}

export interface RenderRuntimeDependency {
  readonly id: string;
  readonly version: string;
  readonly integrity?: string;
  readonly purpose: "layout" | "chart" | "font" | "export";
}

export interface RenderFontDependency {
  readonly family: string;
  readonly asset_id?: string;
  readonly sha256?: string;
  readonly status: "ready";
}

export interface RenderAssetDependency {
  readonly asset_id: string;
  readonly kind: "image" | "font" | "document" | "template" | "generated";
  readonly mime_type?: string;
  readonly sha256?: string;
  readonly status: "ready";
}

export interface RenderChartSnapshot {
  readonly chart_id: string;
  readonly slide_id: string;
  readonly mime_type: "image/png" | "image/svg+xml";
  readonly sha256: string;
  readonly asset_id: string;
}

export interface RenderChartSnapshotManifest {
  readonly status: "not-required" | "runtime-required" | "complete";
  readonly chart_ids: readonly string[];
  readonly snapshots: readonly RenderChartSnapshot[];
}

export interface RenderScriptPolicy {
  readonly mode: "deny" | "controlled-runtime";
  readonly allow_inline: false;
  readonly allow_eval: false;
  readonly allow_external: false;
  readonly iframe_free: boolean;
}

export interface RenderSelectionMapEntry {
  readonly slide_id: string;
  readonly object_ids: readonly string[];
}

export interface RenderOverflowValidation {
  readonly status: "passed" | "failed";
  readonly overflow_object_ids: readonly string[];
}

export interface RenderSlideDiscovery {
  readonly method: "persisted-slide-array";
  readonly count: number;
  readonly slide_ids: readonly string[];
}

export interface RenderArtifact<Payload = unknown> {
  readonly schema_version: typeof RENDER_ARTIFACT_CONTRACT;
  readonly artifact_id: string;
  readonly artifact_version: string;
  readonly target: RenderTarget;
  readonly driver_id: PresentationDriverId;
  readonly renderer_version: string;
  readonly source: RenderSourceIdentity;
  readonly runtime_dependencies: readonly RenderRuntimeDependency[];
  readonly fonts: readonly RenderFontDependency[];
  readonly assets: readonly RenderAssetDependency[];
  readonly chart_snapshot: RenderChartSnapshotManifest;
  readonly script_policy: RenderScriptPolicy;
  readonly selection_map: readonly RenderSelectionMapEntry[];
  readonly slide_discovery: RenderSlideDiscovery;
  readonly overflow_validation: RenderOverflowValidation;
  readonly infographic_policy_version: "a6552331";
  readonly payload: Payload;
}

export interface RenderPresentationSnapshot {
  readonly source: RenderSourceIdentity;
  readonly profile: PresentationProfile;
  readonly capabilities: PresentationCapabilities;
  readonly presentation: Readonly<Record<string, unknown>>;
  readonly slides: readonly Readonly<Record<string, unknown>>[];
}

export interface RenderRequest {
  readonly target: RenderTarget;
  readonly snapshot: RenderPresentationSnapshot;
  readonly expected_revision?: number;
  readonly expected_artifact_version?: string;
  readonly fonts?: readonly RenderFontDependency[];
  readonly assets?: readonly RenderAssetDependency[];
  readonly chart_snapshots?: readonly RenderChartSnapshot[];
  readonly template_render_plans?: readonly TemplateRenderPlan[];
}

export interface RenderAdapterPlan<Payload = unknown> {
  readonly renderer_version: string;
  readonly runtime_dependencies: readonly RenderRuntimeDependency[];
  readonly payload: Payload;
}

export interface PresentationRenderAdapter {
  readonly driver_id: PresentationDriverId;
  readonly renderer_adapter_id: string;
  plan(request: RenderRequest): RenderAdapterPlan;
}

export type PresentationRenderAdapterRegistry = Readonly<{
  [DriverId in PresentationDriverId]: PresentationRenderAdapter & {
    readonly driver_id: DriverId;
  };
}>;
