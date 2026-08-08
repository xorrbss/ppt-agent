"use client";

import { useEffect, useState, type ChangeEvent } from "react";

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
  template_v2_local_image_filename_invalid: "The local filename is invalid.",
  template_v2_local_image_type_not_allowed:
    "Only local PNG, JPEG, and WebP files are allowed.",
  template_v2_local_image_empty: "The selected file is empty.",
  template_v2_local_image_bytes_exceeded:
    "The file exceeds the 10 MB local upload limit.",
  template_v2_local_image_magic_mismatch:
    "The declared MIME type does not match the file magic bytes.",
  template_v2_local_image_decode_failed:
    "The image could not be decoded safely.",
  template_v2_local_image_dimension_exceeded:
    "Image width and height must each be between 1 and 8192 pixels.",
  template_v2_local_image_pixels_exceeded:
    "The decoded image exceeds the 40 megapixel limit.",
} as const;

function candidateLabel(candidate: TemplateV2CropCandidate): string {
  if (candidate.strategy === "center") return "Centered";
  if (candidate.strategy === "adaptive_focus") return "Adaptive focus";
  return "Rule of thirds";
}

export default function TemplateV2ImageReplacementPanel({
  element,
  revision,
  disabled,
  onApply,
}: TemplateV2ImageReplacementPanelProps) {
  const [validation, setValidation] =
    useState<TemplateV2ImageValidationResult | null>(null);
  const [preview, setPreview] =
    useState<TemplateV2ImageReplacementPreview | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (preview && preview.expectedRevision !== revision) {
      setError(
        "This preview is stale because the template revision changed. Choose the file again.",
      );
    }
  }, [preview, revision]);

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
        setError(nextPreview.code);
        return;
      }
      setPreview(nextPreview.preview);
      setCandidateId(nextPreview.preview.cropCandidates[0]?.candidateId ?? null);
    } catch {
      setError("The local file could not be read.");
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
        setError(result.code);
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
            Safe local image replacement
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            Local bytes only. Remote URLs, R2 uploads, and network fetches are
            not available.
          </p>
        </div>
        <label className="shrink-0 rounded border border-violet-500/60 px-3 py-1.5 text-xs text-violet-200">
          {busy ? "Checking…" : "Choose local image"}
          <input
            className="sr-only"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            disabled={disabled || busy}
            onChange={chooseFile}
            aria-label="Choose local replacement image"
          />
        </label>
      </div>

      {validation && !validation.ok ? (
        <p role="alert" className="mt-3 text-xs text-red-300">
          {VALIDATION_LABELS[validation.code]}
          <span className="mt-1 block font-mono text-[10px] text-red-400">
            {validation.code}
          </span>
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
            aria-label="Local image validation results"
            className="grid grid-cols-2 gap-x-3 gap-y-2 rounded bg-slate-950 p-3 text-xs"
          >
            <div>
              <dt className="text-slate-500">MIME + magic bytes</dt>
              <dd className="text-emerald-300">
                Valid · {validation.asset.mediaType}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">File size</dt>
              <dd>{validation.asset.sizeBytes.toLocaleString()} bytes</dd>
            </div>
            <div>
              <dt className="text-slate-500">Dimensions</dt>
              <dd>
                {validation.asset.width} × {validation.asset.height} px
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Decoded pixels</dt>
              <dd>{validation.asset.pixelCount.toLocaleString()}</dd>
            </div>
          </dl>

          <div
            className="relative h-40 overflow-hidden rounded border border-slate-700 bg-slate-950"
            aria-label="Image crop preview"
          >
            {/* The source is produced exclusively from validated caller bytes. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              alt="Local replacement crop preview"
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
              Deterministic crop candidates
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
                    {candidateLabel(candidate)}
                  </span>
                  <span className="font-mono text-[10px] text-slate-500">
                    {candidate.reasonCode} · focus {candidate.focusX}/
                    {candidate.focusY} · {candidate.cropScale}×
                  </span>
                </span>
              </label>
            ))}
          </fieldset>

          <p className="text-[11px] text-slate-500">
            Applying records local-upload provenance and defers orphan cleanup;
            the prior asset is never deleted immediately.
          </p>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={cancel}
              className="rounded border border-slate-700 px-3 py-1.5 text-xs"
            >
              Cancel preview
            </button>
            <button
              type="button"
              disabled={disabled || busy || stale || !candidateId}
              onClick={apply}
              className="rounded bg-violet-500 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
            >
              Apply local replacement
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
