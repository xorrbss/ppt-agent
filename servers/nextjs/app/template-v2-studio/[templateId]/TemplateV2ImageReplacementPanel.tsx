"use client";

import { useEffect, useState, type ChangeEvent } from "react";
import { useTranslations } from "next-intl";

import {
  applyTemplateV2ImageReplacement,
  createTemplateV2ImageReplacementPreview,
  decodeTemplateV2ImageInBrowser,
  validateTemplateV2LocalImage,
  type TemplateV2CropCandidate,
  type TemplateV2ImageReplacementPatch,
  type TemplateV2ImageReplacementPreview,
  type TemplateV2ImageValidationResult,
} from "@/lib/template-v2-image-replacement";
import type { JsonRecord } from "@/lib/template-v2-studio";

export interface TemplateV2ImageReplacementApplyIntent {
  patch: TemplateV2ImageReplacementPatch;
  expectedRevision: number;
  idempotencyKey: string;
  previewId: string;
}

interface TemplateV2ImageReplacementPanelProps {
  element: JsonRecord;
  revision: number;
  disabled: boolean;
  onApply: (
    intent: TemplateV2ImageReplacementApplyIntent,
    historyKey: string,
  ) => void;
}

const VALIDATION_LABELS = {
  template_v2_local_image_filename_invalid:
    "imageReplacement.validationErrors.filenameInvalid",
  template_v2_local_image_type_not_allowed:
    "imageReplacement.validationErrors.typeNotAllowed",
  template_v2_local_image_empty: "imageReplacement.validationErrors.empty",
  template_v2_local_image_bytes_exceeded:
    "imageReplacement.validationErrors.bytesExceeded",
  template_v2_local_image_magic_mismatch:
    "imageReplacement.validationErrors.magicMismatch",
  template_v2_local_image_decode_failed:
    "imageReplacement.validationErrors.decodeFailed",
  template_v2_local_image_dimension_exceeded:
    "imageReplacement.validationErrors.dimensionExceeded",
  template_v2_local_image_pixels_exceeded:
    "imageReplacement.validationErrors.pixelsExceeded",
} as const;

function candidateLabelKey(candidate: TemplateV2CropCandidate) {
  if (candidate.strategy === "center") {
    return "imageReplacement.candidates.centered";
  }
  if (candidate.strategy === "adaptive_focus") {
    return "imageReplacement.candidates.adaptiveFocus";
  }
  return "imageReplacement.candidates.ruleOfThirds";
}

function cropReasonKey(candidate: TemplateV2CropCandidate) {
  if (candidate.reasonCode === "CENTER_SAFE_CROP") {
    return "imageReplacement.cropReasons.centerSafe";
  }
  if (candidate.reasonCode === "LANDSCAPE_SAFE_CROP") {
    return "imageReplacement.cropReasons.landscapeSafe";
  }
  if (candidate.reasonCode === "PORTRAIT_SAFE_CROP") {
    return "imageReplacement.cropReasons.portraitSafe";
  }
  if (candidate.reasonCode === "SQUARE_SAFE_CROP") {
    return "imageReplacement.cropReasons.squareSafe";
  }
  return "imageReplacement.cropReasons.ruleOfThirds";
}

function replacementErrorKey(code: string) {
  if (code === "template_v2_local_image_target_not_image") {
    return "imageReplacement.errors.targetNotImage";
  }
  if (code === "template_v2_local_image_revision_invalid") {
    return "imageReplacement.errors.revisionInvalid";
  }
  if (code === "template_v2_local_image_crop_unsupported_icon") {
    return "imageReplacement.errors.cropUnsupportedIcon";
  }
  if (code === "template_v2_local_image_stale_revision") {
    return "imageReplacement.errors.staleRevision";
  }
  if (code === "template_v2_local_image_preview_stale") {
    return "imageReplacement.errors.previewStale";
  }
  if (code === "template_v2_local_image_crop_candidate_unknown") {
    return "imageReplacement.errors.unknownCandidate";
  }
  return "imageReplacement.errors.unavailable";
}

export default function TemplateV2ImageReplacementPanel({
  element,
  revision,
  disabled,
  onApply,
}: TemplateV2ImageReplacementPanelProps) {
  const t = useTranslations("studio");
  const [validation, setValidation] =
    useState<TemplateV2ImageValidationResult | null>(null);
  const [preview, setPreview] =
    useState<TemplateV2ImageReplacementPreview | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (preview && preview.expectedRevision !== revision) {
      setError(t("imageReplacement.errors.stalePreview"));
    }
  }, [preview, revision, t]);

  if (element.type !== "image") return null;

  const selectedCandidate =
    preview?.cropCandidates.find(
      (candidate) => candidate.candidateId === candidateId,
    ) ?? null;
  const stale = Boolean(preview && preview.expectedRevision !== revision);

  function cancel() {
    setValidation(null);
    setPreview(null);
    setCandidateId(null);
    setError(null);
  }

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setError(null);
    setPreview(null);
    setCandidateId(null);
    try {
      const result = await validateTemplateV2LocalImage({
        filename: file.name,
        declaredMediaType: file.type,
        bytes: new Uint8Array(await file.arrayBuffer()),
        decode: decodeTemplateV2ImageInBrowser,
      });
      setValidation(result);
      if (!result.ok) return;
      const nextPreview = await createTemplateV2ImageReplacementPreview({
        element,
        asset: result.asset,
        revision,
      });
      if (!nextPreview.ok) {
        setError(t(replacementErrorKey(nextPreview.code)));
        return;
      }
      setPreview(nextPreview.preview);
      setCandidateId(
        nextPreview.preview.cropCandidates[0]?.candidateId ?? null,
      );
    } catch {
      setError(t("imageReplacement.errors.readFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!preview || !candidateId || stale || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await applyTemplateV2ImageReplacement({
        element,
        preview,
        candidateId,
        currentRevision: revision,
      });
      if (!result.ok) {
        setError(t(replacementErrorKey(result.code)));
        return;
      }
      onApply(
        {
          patch: result.patch,
          expectedRevision: preview.expectedRevision,
          idempotencyKey: preview.idempotencyKey,
          previewId: preview.previewId,
        },
        `image-replacement-${preview.previewId}`,
      );
      cancel();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-5 rounded-lg border border-slate-800 p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-slate-100">
            {t("imageReplacement.title")}
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            {t("imageReplacement.description")}
          </p>
        </div>
        <label className="shrink-0 rounded border border-violet-500/60 px-3 py-1.5 text-xs text-violet-200">
          {busy
            ? t("imageReplacement.checking")
            : t("imageReplacement.chooseLocalImage")}
          <input
            className="sr-only"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            disabled={disabled || busy}
            onChange={chooseFile}
            aria-label={t("imageReplacement.chooseAria")}
          />
        </label>
      </div>

      {validation && !validation.ok ? (
        <p role="alert" className="mt-3 text-xs text-red-300">
          {t(VALIDATION_LABELS[validation.code])}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-3 text-xs text-red-300">
          {error}
        </p>
      ) : null}

      {validation?.ok && preview ? (
        <div className="mt-4 space-y-3">
          <dl
            aria-label={t("imageReplacement.validationResultsAria")}
            className="grid grid-cols-2 gap-x-3 gap-y-2 rounded bg-slate-950 p-3 text-xs"
          >
            <div>
              <dt className="text-slate-500">
                {t("imageReplacement.mimeMagic")}
              </dt>
              <dd className="text-emerald-300">
                {t("imageReplacement.validMediaType", {
                  mediaType: validation.asset.mediaType,
                })}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">
                {t("imageReplacement.fileSize")}
              </dt>
              <dd>
                {t("imageReplacement.fileSizeValue", {
                  size: validation.asset.sizeBytes.toLocaleString(),
                })}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">
                {t("imageReplacement.dimensions")}
              </dt>
              <dd>
                {t("imageReplacement.dimensionsValue", {
                  width: validation.asset.width,
                  height: validation.asset.height,
                })}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">
                {t("imageReplacement.decodedPixels")}
              </dt>
              <dd>{validation.asset.pixelCount.toLocaleString()}</dd>
            </div>
          </dl>

          <div
            className="relative h-40 overflow-hidden rounded border border-slate-700 bg-slate-950"
            aria-label={t("imageReplacement.previewAria")}
          >
            {/* The source is produced exclusively from validated caller bytes. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              alt={t("imageReplacement.previewAlt")}
              src={validation.asset.dataUrl}
              className="h-full w-full object-cover"
              style={{
                objectPosition: `${selectedCandidate?.focusX ?? 50}% ${
                  selectedCandidate?.focusY ?? 50
                }%`,
                transform: `scale(${selectedCandidate?.cropScale ?? 1})`,
              }}
            />
          </div>

          <fieldset className="space-y-2">
            <legend className="text-xs font-medium text-slate-300">
              {t("imageReplacement.cropLegend")}
            </legend>
            {preview.cropCandidates.map((candidate) => (
              <label
                key={candidate.candidateId}
                className="flex cursor-pointer items-start gap-2 rounded border border-slate-800 p-2 text-xs"
              >
                <input
                  type="radio"
                  name={`crop-${preview.previewId}`}
                  checked={candidate.candidateId === candidateId}
                  disabled={disabled || busy || stale}
                  onChange={() => setCandidateId(candidate.candidateId)}
                />
                <span>
                  <span className="block text-slate-200">
                    {t(candidateLabelKey(candidate))}
                  </span>
                  <span className="font-mono text-[10px] text-slate-500">
                    {t("imageReplacement.cropDetails", {
                      reason: t(cropReasonKey(candidate)),
                      focusX: candidate.focusX,
                      focusY: candidate.focusY,
                      scale: candidate.cropScale,
                    })}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>

          <p className="text-[11px] text-slate-500">
            {t("imageReplacement.provenanceNote")}
          </p>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={cancel}
              className="rounded border border-slate-700 px-3 py-1.5 text-xs"
            >
              {t("imageReplacement.cancelPreview")}
            </button>
            <button
              type="button"
              disabled={disabled || busy || stale || !candidateId}
              onClick={apply}
              className="rounded bg-violet-500 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
            >
              {t("imageReplacement.apply")}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
