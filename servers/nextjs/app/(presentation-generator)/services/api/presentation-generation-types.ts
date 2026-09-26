/**
 * Wire shapes for the presentation-generation API client.
 *
 * Split out of `presentation-generation.ts` so the client class stays within
 * the repository file-size budget. `presentation-generation.ts` re-exports
 * every public name here, so importers keep using that module.
 */

export type BlankPresentationResponse = {
  id: string;
  version: number;
  title: string | null;
  n_slides: number;
  language: string;
  fonts?: Record<string, string> | null;
  slides: Array<Record<string, unknown>>;
};

export type GenerationStage =
  | "author"
  | "illustrate"
  | "render"
  | "persist"
  | "finalize"
  | "completed";

export type GenerationProgress = {
  completed_slides: number;
  total_slides: number;
  percent?: number;
  current_slide?: number | null;
  current_stage?: GenerationStage | null;
  stage_counts?: Partial<Record<GenerationStage, number>>;
  finalizing?: boolean;
};

export type GenerationFailure = {
  code?: string;
  message?: string;
  stage?: GenerationStage;
  slide_number?: number;
  exception_type?: string;
  fingerprint?: string;
  retryable?: boolean;
  request_id?: string | null;
  status_code?: number;
  detail?: unknown;
};

export type GenerationStatusResponse = {
  id?: string;
  status: string;
  message?: string | null;
  error?: GenerationFailure | Record<string, unknown> | null;
  // Free-form task payload. Left as `any` values on purpose: consumers read
  // fields such as `data.edit_path` directly, and narrowing them here would
  // require edits in `outline/hooks/usePresentationGeneration.ts`.
  data?: Record<string, any> | null;
  request_id?: string;
  progress?: GenerationProgress | null;
  failure?: GenerationFailure | null;
};

export type StartGenerationResponse = {
  id?: string;
  status?: string;
  operation_id?: string;
  request_id?: string;
};

export type DocumentDecomposeProgress = {
  completed_files: number;
  total_files: number;
  current_file?: string | null;
};

export type DocumentDecomposeFailure = {
  code?: string;
  message?: string;
  filename?: string;
  retryable?: boolean;
};

export type DecomposedDocument = {
  name: string;
  asset_id?: string;
  content_sha256?: string;
  /** Legacy synchronous decomposition facade only. */
  file_path?: string;
  [key: string]: unknown;
};

export type CanonicalDocumentAsset = {
  asset_id: string;
  kind: "document";
  state: string;
  media_type?: string | null;
  size_bytes: number;
  sha256: string;
  version: number;
  expires_at?: string | null;
};

export type DocumentUploadAdmission = {
  assets: CanonicalDocumentAsset[];
  assetIds: string[];
  referenceSnapshotId: string;
};

export type DecomposeDocumentSource =
  | { referenceSnapshotId: string; documentCount: number }
  | string[];

export type DocumentDecomposeJob = {
  job_id: string;
  status:
    | "queued"
    | "running"
    | "cancelling"
    | "succeeded"
    | "failed"
    | "cancelled";
  progress: DocumentDecomposeProgress;
  result: DecomposedDocument[] | null;
  error: DocumentDecomposeFailure | null;
  created_at?: string;
  updated_at?: string;
  expires_at?: string;
};

export type DecomposeDocumentsOptions = {
  signal?: AbortSignal;
  onProgress?: (job: DocumentDecomposeJob) => void;
  pollIntervalMs?: number;
  idempotencyKey?: string;
};
