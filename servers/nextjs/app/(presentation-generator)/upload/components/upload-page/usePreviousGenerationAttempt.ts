"use client";

import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { useSearchParams } from "next/navigation";
import { DashboardApi, type PresentationGenerationInfo } from "../../../services/api/dashboard";
import { LanguageType, ToneType, VerbosityType, type PresentationConfig } from "../../type";

const ATTEMPT_PROMPT_HANDOFF_PREFIX = "presenton.previous-attempt-prompt.v1:";

function attemptPromptHandoffKey(presentationId: string): string {
  return `${ATTEMPT_PROMPT_HANDOFF_PREFIX}${encodeURIComponent(presentationId)}`;
}

export function stagePreviousAttemptPromptEdit(
  presentationId: string,
  prompt: string,
): boolean {
  try {
    window.sessionStorage.setItem(
      attemptPromptHandoffKey(presentationId),
      JSON.stringify({ presentationId, prompt }),
    );
    return true;
  } catch {
    return false;
  }
}

function consumePreviousAttemptPromptEdit(
  presentationId: string,
): string | null {
  const key = attemptPromptHandoffKey(presentationId);
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    window.sessionStorage.removeItem(key);
    const value = JSON.parse(raw) as {
      presentationId?: unknown;
      prompt?: unknown;
    };
    if (
      value.presentationId !== presentationId ||
      typeof value.prompt !== "string"
    ) {
      return null;
    }
    return value.prompt;
  } catch {
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      // Session storage is optional; the server snapshot remains authoritative.
    }
    return null;
  }
}

/** A changed request gets new inputs and keys; previous files are never reused by ID. */
export function usePreviousGenerationAttempt(
  setConfig: Dispatch<SetStateAction<PresentationConfig>>,
  recoveringAdmission: boolean,
) {
  const params = useSearchParams();
  const requestedId = params?.get("previous") ?? null;
  const prefilledId = useRef<string | null>(null);
  const [state, setState] = useState<{
    id: string | null;
    info: PresentationGenerationInfo | null;
    error: string | null;
  }>({ id: null, info: null, error: null });
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!requestedId) return;
    if (recoveringAdmission) {
      // Admission recovery owns the already edited, immutable configuration.
      prefilledId.current = requestedId;
      return;
    }
    let disposed = false;
    setState({ id: requestedId, info: null, error: null });
    void DashboardApi.getPresentationGenerationInfo(requestedId).then((info) => {
      if (disposed) return;
      if (!info.can_start_new_attempt) {
        setState({ id: requestedId, info: null, error: "이전 작업이 종료되었는지 확인한 뒤 요청을 수정할 수 있습니다." });
        return;
      }
      const settings = info.settings;
      if (prefilledId.current !== requestedId) {
        prefilledId.current = requestedId;
        const editedPrompt = consumePreviousAttemptPromptEdit(requestedId);
        setConfig((current) => ({
        ...current,
        prompt: editedPrompt ?? info.prompt,
        slides: settings.n_slides > 0 ? String(settings.n_slides) : current.slides,
        language: Object.values(LanguageType).includes(settings.language as LanguageType) ? settings.language as LanguageType : current.language,
        tone: Object.values(ToneType).includes(settings.tone as ToneType) ? settings.tone as ToneType : current.tone,
        verbosity: Object.values(VerbosityType).includes(settings.verbosity as VerbosityType) ? settings.verbosity as VerbosityType : current.verbosity,
        instructions: settings.instructions ?? "",
        includeTableOfContents: settings.include_table_of_contents,
        includeTitleSlide: settings.include_title_slide,
        webSearch: settings.web_search,
        }));
      }
      setState({ id: requestedId, info, error: null });
    }).catch(() => {
      if (!disposed) setState({ id: requestedId, info: null, error: "이전 요청을 불러오지 못했습니다. 접근 권한과 연결 상태를 확인해 주세요." });
    });
    return () => { disposed = true; };
  }, [requestedId, recoveringAdmission, refresh, setConfig]);
  const info = state.id === requestedId ? state.info : null;
  const error = state.id === requestedId ? state.error : null;
  return {
    requestedId,
    presentationId: info ? requestedId : null,
    info,
    error,
    blocked: Boolean(requestedId && !info),
    retry: () => setRefresh((value) => value + 1),
  };
}
