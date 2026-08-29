import {
  sfntHasCodePoint,
  type FontEmbeddingPlan,
} from "./font-embedding-policy.ts";
import {
  embeddedFaceForStyle,
  resolvePowerPointTypeface,
  type AuthoredEmbeddedTypefaceFaceEvidence,
} from "./font-policy.ts";
import { flattenPreparedNativeElements } from "./native-plan.ts";
import { EDITABLE_HYBRID_SHADOW_PROFILE_ID } from "./presentation-mode.ts";
import {
  embedPowerPointFonts,
  type PowerPointTypefaceInput,
} from "./pptx-font-embedding.ts";
import type { AuthoredHybridSlideLayer } from "./pptx-assembler.ts";
import type { ActiveEmissionExportBinding } from "./active-emission.ts";
import type { PresentationExportFontEmbeddingStatus } from "../presentation-export-quality.ts";

const KOREAN_NATIVE_EMBEDDING_FAMILY = "Noto Sans KR";

export interface PreparedFontEmbedding {
  pptx: Buffer;
  status: PresentationExportFontEmbeddingStatus;
  embeddedTypefaceFamilies: readonly string[];
  embeddedTypefaceFaces: readonly AuthoredEmbeddedTypefaceFaceEvidence[];
}

export function normalizedTypeface(value: string): string {
  return value.trim().replace(/^['"]|['"]$/g, "").toLocaleLowerCase("en-US");
}

export function nativeFontUsage(
  layers: readonly AuthoredHybridSlideLayer[],
  embedding: PreparedFontEmbedding,
): Pick<
  PresentationExportFontEmbeddingStatus,
  | "nativeTextUsage"
  | "nativeTextElements"
  | "nativeTextElementsUsingEmbeddedTypefaces"
  | "nativeTypefaceFamilies"
> {
  const textElements = layers.flatMap((layer) =>
    flattenPreparedNativeElements(layer.elements).filter(
      (element) => element.kind === "text",
    ),
  );
  const usedFamilies = new Set<string>();
  let usingEmbedded = 0;
  for (const element of textElements) {
    const usesEmbedded = element.source.text.runs.some((run) => {
      const face = embeddedFaceForStyle(run.style);
      const faceFamilies = new Set(
        embedding.embeddedTypefaceFaces
          .filter((entry) => entry.face === face)
          .map((entry) => entry.typeface),
      );
      if (!faceFamilies.size) return false;
      const normalizedEmbedded = new Set(
        [...faceFamilies].map(normalizedTypeface),
      );
      const latin = resolvePowerPointTypeface(run.style.fontFamilies, "Aptos", {
        preserveAuthoredFamilies: faceFamilies,
      });
      const eastAsian = normalizedEmbedded.has(normalizedTypeface(latin))
        ? latin
        : resolvePowerPointTypeface(
            run.style.cjkFallbackFamilies,
            process.platform === "win32" ? "Malgun Gothic" : "Noto Sans CJK KR",
            { preserveAuthoredFamilies: faceFamilies },
          );
      for (const typeface of [latin, eastAsian]) {
        if (normalizedEmbedded.has(normalizedTypeface(typeface))) {
          usedFamilies.add(typeface);
          return true;
        }
      }
      return false;
    });
    if (usesEmbedded) usingEmbedded += 1;
  }
  const nativeTextElements = textElements.length;
  const nativeTextUsage = !embedding.status.applied
    ? "not-embedded"
    : nativeTextElements === 0
      ? "raster-only-unused"
      : usingEmbedded > 0
        ? "used"
        : "not-used";
  return {
    nativeTextUsage,
    nativeTextElements,
    nativeTextElementsUsingEmbeddedTypefaces: usingEmbedded,
    nativeTypefaceFamilies: [...usedFamilies].sort(),
  };
}

function fontEmbeddingFailures(
  plan: FontEmbeddingPlan,
): NonNullable<PresentationExportFontEmbeddingStatus["failures"]> {
  return plan.status.failures.map((failure) => ({
    family: failure.family,
    ...(failure.face ? { face: failure.face } : {}),
    reason: failure.reason,
    detail: failure.detail,
  }));
}

function powerPointFontInputs(
  plan: FontEmbeddingPlan,
): PowerPointTypefaceInput[] {
  const filesById = new Map(plan.files.map((file) => [file.sourceId, file]));
  const byFamily = new Map<
    string,
    Partial<
      Record<
        "regular" | "bold" | "italic" | "boldItalic",
        { data: Buffer; subset: boolean; source: string; collectionFaceIndex?: number }
      >
    >
  >();
  for (const face of plan.faces) {
    const file = filesById.get(face.sourceId);
    if (!file) continue;
    const faces = byFamily.get(face.family) ?? {};
    faces[face.face] = {
      data: file.data,
      subset: plan.strategy === "subset",
      source: file.sourceId,
      ...(file.collectionFaceIndex === undefined ? {} : { collectionFaceIndex: file.collectionFaceIndex }),
    };
    byFamily.set(face.family, faces);
  }
  return [...byFamily.entries()].map(([typeface, faces]) => ({
    typeface,
    // Hangul charset and a variable/sans-serif pitch-family value. These values
    // match Office's normal declaration for a Korean sans-serif typeface.
    pitchFamily: 34,
    charset: 129,
    faces,
  }));
}

/** Packages a previously safety-checked fidelity PPTX with its allowed font plan. */
export async function prepareFontEmbedding(
  fidelityPptx: Buffer,
  plan: FontEmbeddingPlan,
): Promise<PreparedFontEmbedding> {
  const failures = fontEmbeddingFailures(plan);
  if (!plan.requested) {
    return {
      pptx: fidelityPptx,
      embeddedTypefaceFamilies: [],
      embeddedTypefaceFaces: [],
      status: {
        policy: "opt-in",
        requested: false,
        applied: false,
        embeddedFontFiles: 0,
        embeddedTypefaces: 0,
        strategy: plan.strategy,
        editLimitation: "none",
        reason: "not-requested",
      },
    };
  }
  if (!plan.status.eligible) {
    throw new Error("authored_hybrid_font_embedding_ineligible");
  }

  try {
    const packaged = embedPowerPointFonts(
      fidelityPptx,
      powerPointFontInputs(plan),
    );
    if (!packaged.result.applied) {
      throw new Error("authored_hybrid_font_embedding_not_applied");
    }

    const filesById = new Map(plan.files.map((file) => [file.sourceId, file]));
    const facesBySource = new Map(
      plan.faces.map((face) => [
        `${face.family}\0${face.sourceId}\0${face.face}`,
        face,
      ]),
    );
    const faceStatus = packaged.result.fonts.flatMap((font) =>
      font.faces.map((face) => {
        const sourceId = face.source ?? "";
        const file = filesById.get(sourceId);
        const plannedFace = facesBySource.get(
          `${font.typeface}\0${sourceId}\0${face.face}`,
        );
        return {
          typeface: font.typeface,
          face: face.face,
          weight: plannedFace?.weight ?? face.weight,
          style: (plannedFace?.style ?? (face.italic ? "italic" : "normal")) as
            "normal" | "italic",
          source: file?.source ?? "server-font-allowlist",
          ...(file?.sourcePath ? { sourcePath: file.sourcePath } : {}),
          sourceSha256: face.sourceSha256,
          sourceBytes: face.sourceBytes,
          embeddedBytes: face.embeddedBytes,
          fsType: face.fsType,
          licenseDecision:
            file?.license.decision ?? ("denied-invalid" as const),
          subset: face.subset,
          strategy: plan.strategy,
          partName: face.partName,
          format: face.format,
          ...(file?.derivedFromVariable ? { derivedFromVariable: true } : {}),
        };
      }),
    );
    const embeddedTypefaceFamilies = packaged.result.fonts.map(
      (font) => font.typeface,
    );
    return {
      pptx: packaged.pptx,
      embeddedTypefaceFamilies,
      embeddedTypefaceFaces: faceStatus.map((face) => ({
        typeface: face.typeface,
        face: face.face,
      })),
      status: {
        policy: "opt-in",
        requested: true,
        applied: true,
        embeddedFontFiles: packaged.result.embeddedFontFiles,
        embeddedTypefaces: packaged.result.embeddedTypefaces,
        strategy: plan.strategy,
        editLimitation:
          plan.strategy === "subset"
            ? "characters-outside-subset-may-substitute"
            : "none",
        faces: faceStatus,
        ...(failures.length ? { failures } : {}),
        reason: "embedded",
      },
    };
  } catch (error) {
    if (
      error instanceof Error &&
      error.message === "authored_hybrid_font_embedding_not_applied"
    ) {
      throw error;
    }
    const stableCollectionReason = error instanceof Error
      ? error.message.match(/^(pptx_font_collection_[a-z_]+)/)?.[1]
      : undefined;
    throw new Error(stableCollectionReason ?? "authored_hybrid_font_embedding_failed", { cause: error });
  }
}

export function embeddedGlyphAvailable(plan: FontEmbeddingPlan) {
  const files = new Map(plan.files.map((file) => [file.sourceId, file] as const));
  const faces = new Map(plan.faces.map((face) => [
    `${normalizedTypeface(face.family)}\0${face.face}`,
    face,
  ] as const));
  return (typeface: string, face: AuthoredEmbeddedTypefaceFaceEvidence["face"], codePoint: number) => {
    const planned = faces.get(`${normalizedTypeface(typeface)}\0${face}`);
    const file = planned ? files.get(planned.sourceId) : undefined;
    if (!file) return false;
    try {
      return sfntHasCodePoint(file.data, codePoint, file.collectionFaceIndex);
    } catch {
      return false;
    }
  };
}

export function nativeEmbeddingFamilies(
  binding: ActiveEmissionExportBinding,
  profileId: string | undefined,
): string[] {
  const authored = binding.slides.flatMap((slide) => slide.fontFamilies);
  const requiresKoreanFace =
    profileId === EDITABLE_HYBRID_SHADOW_PROFILE_ID ||
    authored.some((family) =>
      /(?:noto\s+sans\s+(?:kr|cjk)|pretendard|malgun|맑은\s*고딕)/iu.test(family),
    );
  // Browser CSS may name only the Latin face even when Hangul is resolved by
  // a fallback. The sealed ko/en profile's script-aware serializer always
  // emits Noto Sans KR for Hangul, so include that exact package family in the
  // plan instead of discovering the requirement after packaging.
  return [...new Set([
    ...(requiresKoreanFace ? [KOREAN_NATIVE_EMBEDDING_FAMILY] : []),
    ...authored,
  ])];
}
