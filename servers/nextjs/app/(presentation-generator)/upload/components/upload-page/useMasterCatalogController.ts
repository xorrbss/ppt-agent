"use client";
/**
 * Owns the company-standard "master catalog" dialog state and the AI-import
 * flow that lets a user analyse an uploaded PPTX into a new master: which
 * entry mode opened the dialog, the remembered analysis id (so reopening the
 * dialog can resume it), and applying/clearing the selected master. Extracted
 * from UploadPage.tsx (file-size ratchet split); behaviour unchanged, only
 * the module boundary moved. The document-scoped "reset on mount" effect
 * stays inline in UploadPage.tsx (a source-locked regression test asserts
 * its exact text there); `masterSelectionEpochRef` is returned so that
 * effect can still bump the same epoch counter the Apply/Clear handlers use.
 */
import { useCallback, useRef, useState } from "react";
import type { Dispatch } from "redux";

import { notify } from "@/components/ui/sonner";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import type { MasterRevisionEnvelope } from "@/lib/authored-vnext/master/contracts";
import {
  clearLastUsedMasterId,
  masterSelectionFromPublished,
} from "@/lib/authored-vnext/master/selection-storage";
import type { MasterCatalogEntryMode } from "../../../authored-vnext/master-setup/MasterCatalogDialog";
import { MAX_SUPPORTED_FILES } from "../SupportingDoc";

const LAST_AI_MASTER_ANALYSIS_KEY = "presenton.authored-vnext.ai-master-analysis.v1";

interface UseMasterCatalogControllerArgs {
  dispatch: Dispatch;
  files: File[];
  replaceFiles: (files: File[]) => void;
  analysisSourceFile: File | null;
  uploadLimits: { document: { bytes: number; mb: number } };
}

export const useMasterCatalogController = ({
  dispatch,
  files,
  replaceFiles,
  analysisSourceFile,
  uploadLimits,
}: UseMasterCatalogControllerArgs) => {
  const [masterCatalogOpen, setMasterCatalogOpen] = useState(false);
  const [masterCatalogEntryMode, setMasterCatalogEntryMode] =
    useState<MasterCatalogEntryMode>("catalog");
  const [lastAnalysisId, setLastAnalysisId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const value = window.localStorage.getItem(LAST_AI_MASTER_ANALYSIS_KEY);
      return value && /^[0-9a-f]{32}$/.test(value) ? value : null;
    } catch {
      return null;
    }
  });
  const [analyzedSourceSignature, setAnalyzedSourceSignature] = useState<string | null>(null);
  const masterSelectionEpochRef = useRef(0);

  const analysisSourceSignature = analysisSourceFile
    ? `${analysisSourceFile.name}:${analysisSourceFile.size}:${analysisSourceFile.lastModified}`
    : null;
  // A remembered analysis may only be restored while the PPTX it was produced
  // from is still staged here. Without that guard, "create from PPT" silently
  // reopens an unrelated analysis left over from an earlier document.
  const resumeAnalysisId =
    analysisSourceFile && analyzedSourceSignature === analysisSourceSignature
      ? lastAnalysisId
      : null;

  const openMasterCatalog = useCallback(() => {
    setMasterCatalogEntryMode("catalog");
    setMasterCatalogOpen(true);
  }, []);

  // The catalog lists published masters and opens the blank designer; the only
  // way into the PPTX analysis is this entry mode, so without this control the
  // whole AI-import flow is unreachable from the app.
  const openAiMasterImport = useCallback(() => {
    setMasterCatalogEntryMode("ai-import");
    setMasterCatalogOpen(true);
  }, []);

  const selectAiMasterSource = useCallback((file: File) => {
    if (!/\.(?:ppt|pptx)$/i.test(file.name)) return;
    if (file.size > uploadLimits.document.bytes) {
      notify.error(
        "파일이 너무 큽니다",
        `파일당 최대 크기는 ${uploadLimits.document.mb}MB입니다.`,
      );
      return;
    }
    if (!analysisSourceFile && files.length >= MAX_SUPPORTED_FILES) {
      notify.warning(
        "최대 파일 수 초과",
        `문서는 최대 ${MAX_SUPPORTED_FILES}개까지만 업로드할 수 있습니다.`,
      );
      return;
    }
    const sourceIndex = files.findIndex((item) => /\.(?:ppt|pptx)$/i.test(item.name));
    if (sourceIndex < 0) replaceFiles([...files, file]);
    else {
      const next = [...files];
      next[sourceIndex] = file;
      replaceFiles(next);
    }
  }, [analysisSourceFile, files, replaceFiles, uploadLimits.document.bytes, uploadLimits.document.mb]);

  const rememberAnalysis = useCallback((analysisId: string | null) => {
    setLastAnalysisId(analysisId);
    setAnalyzedSourceSignature(analysisId ? analysisSourceSignature : null);
    try {
      if (analysisId) window.localStorage.setItem(LAST_AI_MASTER_ANALYSIS_KEY, analysisId);
      else window.localStorage.removeItem(LAST_AI_MASTER_ANALYSIS_KEY);
    } catch {
      // Server persistence remains authoritative when browser storage is unavailable.
    }
  }, [analysisSourceSignature]);

  const applyMaster = useCallback(
    (published: MasterRevisionEnvelope) => {
      // A manual Apply always wins over an in-flight preference restoration.
      ++masterSelectionEpochRef.current;
      dispatch(
        setPptGenUploadState({
          companyStandard: masterSelectionFromPublished(published),
        })
      );
    },
    [dispatch]
  );

  const clearMaster = useCallback(() => {
    ++masterSelectionEpochRef.current;
    dispatch(setPptGenUploadState({ companyStandard: null }));
    clearLastUsedMasterId();
  }, [dispatch]);

  return {
    masterCatalogOpen,
    setMasterCatalogOpen,
    masterCatalogEntryMode,
    resumeAnalysisId,
    openMasterCatalog,
    openAiMasterImport,
    selectAiMasterSource,
    rememberAnalysis,
    applyMaster,
    clearMaster,
    masterSelectionEpochRef,
  };
};
