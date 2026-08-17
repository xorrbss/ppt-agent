"use client";

import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

import { buildSafeExportFileName } from "../utils/buildSafeExportFileName";
import { presentationMasterPin } from "../utils/masterPresentation";

const EDIT_WARMUP_DELAY_MS = 20_000;

interface UseHybridExportWarmupOptions {
  presentationId: string;
  title: string | null | undefined;
  presentationData: unknown;
  isAuthoredDeck: boolean;
  isStreaming: boolean | null;
}

export const useHybridExportWarmup = ({
  presentationId,
  title,
  presentationData,
  isAuthoredDeck,
  isStreaming,
}: UseHybridExportWarmupOptions) => {
  const currentStateRef = useRef({
    presentationId,
    title,
    presentationData,
    isAuthoredDeck,
    isStreaming,
  });

  const previousPresentationIdRef = useRef(presentationId);
  const previousStreamingRef = useRef(isStreaming);
  const baselineDataRef = useRef(presentationData);
  const hasBaselineDataRef = useRef(
    presentationData !== null && presentationData !== undefined
  );
  const editWarmupTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  useLayoutEffect(() => {
    currentStateRef.current = {
      presentationId,
      title,
      presentationData,
      isAuthoredDeck,
      isStreaming,
    };
  }, [isAuthoredDeck, isStreaming, presentationData, presentationId, title]);

  const warmHybridExport = useCallback(() => {
    const current = currentStateRef.current;
    if (
      !current.isAuthoredDeck ||
      current.isStreaming ||
      presentationMasterPin(current.presentationData)
    ) return;

    const safeFileName = buildSafeExportFileName(current.title, "pptx");
    const safeTitle = safeFileName.replace(/\.pptx$/i, "");

    void fetch("/api/export-presentation", {
      method: "POST",
      body: JSON.stringify({
        format: "pptx",
        id: current.presentationId,
        title: safeTitle,
        pptxMode: "hybrid",
      }),
    })
      .then((response) => {
        if (!response.ok) {
          console.debug("Hybrid export cache warmup failed", response.status);
        }
      })
      .catch((error) => {
        console.debug("Hybrid export cache warmup failed", error);
      });
  }, []);

  const scheduleWarmupAfterSave = useCallback((savedData: unknown) => {
    const current = currentStateRef.current;
    if (!mountedRef.current || !current.isAuthoredDeck || current.isStreaming) return;
    if (hasBaselineDataRef.current && savedData === baselineDataRef.current) return;
    if (savedData !== current.presentationData) return;

    if (editWarmupTimerRef.current) {
      clearTimeout(editWarmupTimerRef.current);
    }
    editWarmupTimerRef.current = setTimeout(() => {
      editWarmupTimerRef.current = null;
      warmHybridExport();
    }, EDIT_WARMUP_DELAY_MS);
  }, [warmHybridExport]);

  useEffect(() => {
    const presentationChanged = previousPresentationIdRef.current !== presentationId;
    previousPresentationIdRef.current = presentationId;

    if (presentationChanged) {
      if (editWarmupTimerRef.current) {
        clearTimeout(editWarmupTimerRef.current);
        editWarmupTimerRef.current = null;
      }
      previousStreamingRef.current = isStreaming;
      baselineDataRef.current = presentationData;
      hasBaselineDataRef.current =
        presentationData !== null && presentationData !== undefined;
      return;
    }

    const wasStreaming = previousStreamingRef.current;
    previousStreamingRef.current = isStreaming;

    if (isStreaming) {
      baselineDataRef.current = presentationData;
      hasBaselineDataRef.current =
        presentationData !== null && presentationData !== undefined;
      return;
    }

    if (
      !hasBaselineDataRef.current &&
      presentationData !== null &&
      presentationData !== undefined
    ) {
      baselineDataRef.current = presentationData;
      hasBaselineDataRef.current = true;
    }

    if (wasStreaming === true && isStreaming === false && isAuthoredDeck) {
      baselineDataRef.current = presentationData;
      hasBaselineDataRef.current =
        presentationData !== null && presentationData !== undefined;
      warmHybridExport();
    }
  }, [
    isAuthoredDeck,
    isStreaming,
    presentationData,
    presentationId,
    warmHybridExport,
  ]);

  useEffect(() => {
    if (isAuthoredDeck && !isStreaming) return;
    if (editWarmupTimerRef.current) {
      clearTimeout(editWarmupTimerRef.current);
      editWarmupTimerRef.current = null;
    }
  }, [isAuthoredDeck, isStreaming]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (editWarmupTimerRef.current) {
        clearTimeout(editWarmupTimerRef.current);
      }
    };
  }, []);

  return scheduleWarmupAfterSave;
};
