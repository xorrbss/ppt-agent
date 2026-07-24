import { isAuthoredPresentation } from "./authored-hybrid/presentation-mode.ts";
import type { PptxMode } from "./authored-hybrid/mode.ts";

export type PersistedExportStrategy =
  | "template-v2-general"
  | "authored-hybrid"
  | "legacy-general";

export const PRESENTATION_SOURCE_SHA256 = Symbol(
  "presenton.presentationSourceSha256"
);

export interface PersistedPresentation {
  version?: unknown;
  mode?: unknown;
  theme?: unknown;
  slides?: unknown;
  [PRESENTATION_SOURCE_SHA256]?: string;
}

export interface PresentationExportExecutionParams {
  format: "pdf" | "pptx";
  presentationId: string;
  title?: string;
  cookieHeader?: string;
  pptxMode?: PptxMode;
  expectedPresentationSha256?: string;
}

export interface PresentationExportResult {
  path: string;
}

export interface PresentationExportAdapterRegistry<
  Result extends PresentationExportResult = PresentationExportResult,
> {
  general(
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
    mode !== "adaptive" &&
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
    themeMode !== "adaptive" &&
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
      mode !== "template" ||
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
    return "template-v2-general";
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
    return "authored-hybrid";
  }

  if (
    payloads.some(
      ({ ui, html }) =>
        (ui !== null && ui !== undefined) || Boolean(html)
    )
  ) {
    throw new Error("legacy_payload_identity_mismatch");
  }
  return "legacy-general";
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
    params.format === "pptx" &&
    params.pptxMode === "hybrid" &&
    strategy === "authored-hybrid"
  ) {
    return registry.hybrid(params);
  }
  return registry.general(params);
}
