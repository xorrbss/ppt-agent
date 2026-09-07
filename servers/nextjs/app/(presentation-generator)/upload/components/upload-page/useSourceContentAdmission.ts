"use client";

/**
 * Source-content admission: analyse the uploaded deck, then decide whether the
 * run needs the user's consent before it starts.
 *
 * Preserving original content drops tables and charts, and keeps whatever it
 * cannot recompose as a flat image. That is a real loss of the user's source,
 * so it may not happen silently -- but it does not happen to every deck. This
 * module reports what a given analysis would cost, so the caller can run
 * straight through when the answer is "nothing" and stop for a confirmation
 * only when the answer is not.
 */

import { useRef, useState } from "react";

import { notify } from "@/components/ui/sonner";
import {
  PresentationGenerationApi,
  type DocumentUploadAdmission,
} from "../../../services/api/presentation-generation";
import {
  SourceContentApiV1,
  sourceContentSelectionErrorMessage,
  validateSourceContentFiles,
  type SourceContentAnalysisResponseV1,
  type SourceContentAnalysisSummary,
} from "../../../services/api/source-content/contract";
import { sourceContentLosses } from "./sourceContentLosses";
import { LoadingState } from "./types";

export type SourceContentAdmission = {
  analysis: SourceContentAnalysisResponseV1;
  documentAdmission: DocumentUploadAdmission;
  fileIdentity: string;
};

export type SourceContentLossPrompt = {
  summary: SourceContentAnalysisSummary;
  losses: string[];
};

export { sourceContentLosses };

export const fileIdentityOf = (files: readonly File[]): string =>
  files.map((file) => `${file.name}:${file.size}:${file.lastModified}`).join("|");

type UseSourceContentAdmissionArgs = {
  pendingPresentationId: string;
  setLoadingState: (state: LoadingState) => void;
  trackUploadValidationFailure: (reason: string) => void;
};

export const useSourceContentAdmission = ({
  pendingPresentationId,
  setLoadingState,
  trackUploadValidationFailure,
}: UseSourceContentAdmissionArgs) => {
  const [summary, setSummary] = useState<SourceContentAnalysisSummary | null>(
    null,
  );
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [admission, setAdmission] = useState<SourceContentAdmission | null>(
    null,
  );
  const [lossPrompt, setLossPrompt] = useState<SourceContentLossPrompt | null>(
    null,
  );
  const lossDecisionRef = useRef<((accepted: boolean) => void) | null>(null);

  /**
   * Analyse the selected deck and return its admission. The caller continues
   * with the returned value rather than with `admission`: this all happens in
   * one click now, so the state setter below has not re-rendered yet.
   */
  const analyze = async (
    selectedFiles: File[],
  ): Promise<SourceContentAdmission | null> => {
    const validation = validateSourceContentFiles(selectedFiles);
    if (validation) {
      trackUploadValidationFailure(validation);
      notify.warning(
        "원본 콘텐츠 유지 파일 제한",
        sourceContentSelectionErrorMessage(validation),
      );
      return null;
    }
    setLoadingState({
      isLoading: true,
      message: "원본 콘텐츠를 분석하는 중…",
      showProgress: true,
      duration: 90,
      progress: 10,
      progressLabel: "PPTX 안전 분석 및 콘텐츠 inventory",
      extra_info: "분석이 끝나면 선택한 템플릿으로 바로 재구성합니다.",
    });
    setIsAnalyzing(true);
    let documentAdmission: DocumentUploadAdmission | null = null;
    try {
      documentAdmission = await PresentationGenerationApi.uploadDoc(
        selectedFiles,
        pendingPresentationId,
      );
      const analysis = await SourceContentApiV1.analyze({
        referenceSnapshotId: documentAdmission.referenceSnapshotId,
        idempotencyKey: `source-content-analyze:${pendingPresentationId}:${documentAdmission.referenceSnapshotId}`,
      });
      if (
        analysis.status !== "ready" ||
        !analysis.capabilities.pptx ||
        analysis.capabilities.pdf ||
        analysis.capabilities.docx ||
        analysis.capabilities.ppt ||
        analysis.referenceSnapshotBinding.referenceSnapshotId !==
          documentAdmission.referenceSnapshotId
      ) {
        throw new Error("source_content_manifest_invalid");
      }
      const admitted: SourceContentAdmission = {
        analysis,
        documentAdmission,
        fileIdentity: fileIdentityOf(selectedFiles),
      };
      setAdmission(admitted);
      setSummary(analysis.summary);
      return admitted;
    } catch (error) {
      if (documentAdmission) {
        await PresentationGenerationApi.cleanupDocumentAdmission({
          referenceSnapshotId: documentAdmission.referenceSnapshotId,
          assetIds: documentAdmission.assetIds,
        });
      }
      throw error;
    } finally {
      setIsAnalyzing(false);
    }
  };

  /**
   * Resolves immediately when the redesign costs the user nothing; otherwise
   * opens the confirmation and resolves with what they chose.
   */
  const confirmLosses = (
    analysisSummary: SourceContentAnalysisSummary,
  ): Promise<boolean> => {
    const losses = sourceContentLosses(analysisSummary);
    if (losses.length === 0) return Promise.resolve(true);
    setLoadingState({
      isLoading: false,
      message: "",
      duration: 0,
      showProgress: false,
    });
    return new Promise<boolean>((resolve) => {
      lossDecisionRef.current = resolve;
      setLossPrompt({ summary: analysisSummary, losses });
    });
  };

  /** A new selection invalidates whatever the previous one was admitted as. */
  const reset = () => {
    setAdmission(null);
    setSummary(null);
  };

  const resolveLossPrompt = (accepted: boolean) => {
    setLossPrompt(null);
    const decide = lossDecisionRef.current;
    lossDecisionRef.current = null;
    decide?.(accepted);
  };

  return {
    admission,
    summary,
    isAnalyzing,
    lossPrompt,
    analyze,
    confirmLosses,
    resolveLossPrompt,
    reset,
  };
};
