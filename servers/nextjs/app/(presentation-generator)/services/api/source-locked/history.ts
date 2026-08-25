import type {
  SourceLockedArtifactV1,
  SourceLockedAnalysisSummary,
  SourceLockedJobResponseV1,
} from "./contract";
import { sourceLockedGenerationFromJob } from "./contract";

/**
 * In-memory dashboard projection for the current browser session. Reload
 * recovery comes exclusively from the owner-scoped server list endpoint;
 * no source-locked metadata is persisted in browser storage.
 */
export type SourceLockedHistoryEntryV1 = {
  contract_version: "source-locked-history/v1";
  job_id: string;
  status: SourceLockedJobResponseV1["status"];
  source_filename: string;
  source_type?: "ppt" | "pptx" | "pdf";
  progress_stages: SourceLockedJobResponseV1["progress_stages"];
  summary?: SourceLockedAnalysisSummary;
  artifact?: SourceLockedArtifactV1;
  error?: { code?: string; message?: string; stage?: string } | null;
  created_at: string;
  updated_at: string;
};

const MAX_ENTRIES = 50;

export function sourceLockedHistoryEntryFromJob(
  job: SourceLockedJobResponseV1,
  fallbackFilename = "원본 문서",
): SourceLockedHistoryEntryV1 {
  const result = sourceLockedGenerationFromJob(job);
  return {
    contract_version: "source-locked-history/v1",
    job_id: job.job_id,
    status: job.status,
    source_filename: job.source_filename || fallbackFilename,
    source_type: result?.summary.source_format,
    progress_stages: job.progress_stages,
    summary: result?.summary,
    // The browser displays only the server-projected, sealed artifact URL.
    artifact: result?.artifact,
    error: job.error ?? null,
    created_at: job.created_at,
    updated_at: job.updated_at,
  };
}

export function upsertSourceLockedHistory(
  entries: readonly SourceLockedHistoryEntryV1[],
  entry: SourceLockedHistoryEntryV1,
): SourceLockedHistoryEntryV1[] {
  return [entry, ...entries.filter((candidate) => candidate.job_id !== entry.job_id)]
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
    .slice(0, MAX_ENTRIES);
}
