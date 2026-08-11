import {
  getPresentationDriver,
  resolveServerIssuedPresentationProfile,
} from "../presentation-profile.ts";
import { PRESENTATION_RENDER_ADAPTER_REGISTRY } from "./adapters.ts";
import {
  RENDER_ARTIFACT_CONTRACT,
  type PresentationRenderAdapterRegistry,
  type RenderArtifact,
  type RenderPresentationSnapshot,
  type RenderRequest,
  type RenderSourceIdentity,
} from "./contracts.ts";
import {
  assertRenderArtifactFresh,
  buildRenderArtifactVersion,
} from "./freshness.ts";
import {
  buildSelectionMap,
  chartSnapshotManifest,
  discoverSlides,
  RenderPolicyError,
  scriptPolicy,
  validateDependencies,
  validateOverflow,
  validateRuntimeDependencies,
} from "./runtime-policy.ts";

type JsonRecord = Record<string, unknown>;

const SHA256 = /^[0-9a-f]{64}$/i;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function deepFreeze<T>(value: T): T {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.values(value as Record<string, unknown>).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

function requireRevision(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new RenderPolicyError("render_revision_required");
  }
  return Number(value);
}

function sourceDigest(presentation: JsonRecord): string | undefined {
  const value = presentation.source_sha256 ?? presentation.presentation_sha256;
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !SHA256.test(value)) {
    throw new RenderPolicyError("render_source_digest_invalid");
  }
  return value.toLowerCase();
}

export function createRenderPresentationSnapshot(
  presentation: unknown,
  options: {
    readonly source_sha256?: string;
    readonly require_source_sha256?: boolean;
  } = {},
): RenderPresentationSnapshot {
  if (!isRecord(presentation)) {
    throw new RenderPolicyError("render_presentation_required");
  }
  const runtime = resolveServerIssuedPresentationProfile(presentation);
  const slides = presentation.slides;
  if (!Array.isArray(slides) || slides.length === 0 || !slides.every(isRecord)) {
    throw new RenderPolicyError("render_slides_required");
  }
  if (typeof presentation.id !== "string" || !presentation.id) {
    throw new RenderPolicyError("render_presentation_id_required");
  }
  const lifecycle = presentation.lifecycle_status;
  if (lifecycle !== "published" && lifecycle !== "staging") {
    throw new RenderPolicyError("render_presentation_not_renderable");
  }
  const digest = sourceDigest({
    ...presentation,
    ...(options.source_sha256
      ? { source_sha256: options.source_sha256 }
      : {}),
  });
  if (options.require_source_sha256 && !digest) {
    throw new RenderPolicyError("render_source_digest_required");
  }
  const source: RenderSourceIdentity = Object.freeze({
    presentation_id: presentation.id,
    revision: requireRevision(presentation.revision),
    lifecycle_status: lifecycle,
    published: lifecycle === "published",
    ...(digest ? { source_sha256: digest } : {}),
  });
  return deepFreeze({
    source,
    profile: runtime.profile,
    capabilities: runtime.capabilities,
    presentation: deepFreeze(structuredClone(presentation)),
    slides: deepFreeze(structuredClone(slides)),
  });
}

export class RenderService {
  readonly registry: PresentationRenderAdapterRegistry;

  constructor(
    registry: PresentationRenderAdapterRegistry =
      PRESENTATION_RENDER_ADAPTER_REGISTRY,
  ) {
    this.registry = registry;
  }

  render(request: RenderRequest): RenderArtifact {
    const { snapshot } = request;
    if (
      request.expected_revision !== undefined &&
      request.expected_revision !== snapshot.source.revision
    ) {
      throw new RenderPolicyError("stale_render_revision");
    }
    const driver = getPresentationDriver(snapshot.profile.driver_id);
    driver.validate(snapshot.profile, snapshot.capabilities);
    const adapter = this.registry[driver.driver_id];
    if (
      adapter.renderer_adapter_id !== snapshot.capabilities.renderer_adapter_id
    ) {
      throw new RenderPolicyError("render_adapter_capability_mismatch");
    }
    if (
      (request.target === "pdf" || request.target === "pptx") &&
      !snapshot.source.source_sha256
    ) {
      throw new RenderPolicyError("render_source_digest_required");
    }
    validateDependencies(request.fonts, request.assets, snapshot);
    const overflow = validateOverflow(snapshot);
    if (
      overflow.status === "failed" &&
      (request.target === "pdf" || request.target === "pptx")
    ) {
      throw new RenderPolicyError("render_overflow_validation_failed");
    }
    const plan = adapter.plan(request);
    validateRuntimeDependencies(plan.runtime_dependencies);
    const version = buildRenderArtifactVersion(
      snapshot.source,
      plan.renderer_version,
    );
    if (
      request.expected_artifact_version !== undefined &&
      request.expected_artifact_version !== version
    ) {
      throw new RenderPolicyError("stale_render_artifact_version");
    }
    const artifact: RenderArtifact = deepFreeze({
      schema_version: RENDER_ARTIFACT_CONTRACT,
      artifact_id: `${snapshot.source.presentation_id}:${request.target}:${version}`,
      artifact_version: version,
      target: request.target,
      driver_id: driver.driver_id,
      renderer_version: plan.renderer_version,
      source: snapshot.source,
      runtime_dependencies: structuredClone(plan.runtime_dependencies),
      fonts: structuredClone(request.fonts ?? []),
      assets: structuredClone(request.assets ?? []),
      chart_snapshot: chartSnapshotManifest(
        snapshot,
        request.target,
        request.chart_snapshots,
      ),
      script_policy: scriptPolicy(snapshot, request.target),
      selection_map: structuredClone(buildSelectionMap(snapshot)),
      slide_discovery: discoverSlides(snapshot),
      overflow_validation: overflow,
      infographic_policy_version: "a6552331",
      payload: structuredClone(plan.payload),
    });
    assertRenderArtifactFresh(artifact, snapshot.source);
    return artifact;
  }
}
