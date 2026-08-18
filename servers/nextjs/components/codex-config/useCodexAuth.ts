"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { notify } from "@/components/ui/sonner";
import { getApiUrl } from "@/utils/api";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";
import { DEFAULT_CODEX_MODEL } from "./constants";

export type CodexAuthStatus =
  | "checking"
  | "unauthenticated"
  | "polling"
  | "authenticated";

interface StatusResponse {
  status: string;
  account_id?: string;
  username?: string;
  email?: string;
  is_pro?: boolean;
  detail?: string;
}

export interface CodexAuthOptions {
  codexModel: string;
  onInputChange: (value: string | boolean, field: string) => void;
  /**
   * The onboarding surface picks the provider for the user as soon as a stored
   * ChatGPT session is found. The settings surface must not, because the
   * provider there is whatever the operator selected in the dropdown.
   */
  adoptProviderOnSignedIn: boolean;
}

const POLL_INTERVAL_MS = 2000;

export const useCodexAuth = ({
  codexModel,
  onInputChange,
  adoptProviderOnSignedIn,
}: CodexAuthOptions) => {
  const [authStatus, setAuthStatus] = useState<CodexAuthStatus>("checking");
  const [accountId, setAccountId] = useState<string | null>(null);
  const [username, setUsername] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [manualCode, setManualCode] = useState("");
  const [isExchanging, setIsExchanging] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Both call sites pass inline callbacks, so reading them through refs keeps
  // the mount-time status check from re-running on every parent render.
  const onInputChangeRef = useRef(onInputChange);
  const codexModelRef = useRef(codexModel);
  useEffect(() => {
    onInputChangeRef.current = onInputChange;
    codexModelRef.current = codexModel;
  });

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  const applyProfile = useCallback((data: Partial<StatusResponse>) => {
    setAccountId(data.account_id ?? null);
    setUsername(data.username ?? null);
    setEmail(data.email ?? null);
  }, []);

  const markSignedOut = useCallback(() => {
    setAuthStatus("unauthenticated");
    applyProfile({});
  }, [applyProfile]);

  // A stored selection is never overwritten; only an empty model is seeded.
  const seedDefaultModel = useCallback(() => {
    if (!codexModelRef.current) {
      onInputChangeRef.current(DEFAULT_CODEX_MODEL, "codex_model");
    }
  }, []);

  const checkCurrentAuthStatus = useCallback(async () => {
    try {
      const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/status"));
      if (!res.ok) {
        markSignedOut();
        return;
      }
      const data: StatusResponse = await res.json();
      if (data.status !== "authenticated") {
        markSignedOut();
        return;
      }
      if (adoptProviderOnSignedIn) {
        onInputChangeRef.current("codex", "LLM");
      }
      seedDefaultModel();
      setAuthStatus("authenticated");
      applyProfile(data);
    } catch {
      markSignedOut();
    }
  }, [adoptProviderOnSignedIn, applyProfile, markSignedOut, seedDefaultModel]);

  useEffect(() => {
    void checkCurrentAuthStatus();
    return stopPolling;
  }, [checkCurrentAuthStatus, stopPolling]);

  const completeSignIn = useCallback(
    (profile: Partial<StatusResponse>) => {
      stopPolling();
      setAuthStatus("authenticated");
      applyProfile(profile);
      setSessionId(null);
      setManualCode("");
      seedDefaultModel();
      notify.success(
        "ChatGPT 로그인 완료",
        "ChatGPT 계정이 연결되어 사용할 준비가 되었습니다."
      );
    },
    [applyProfile, seedDefaultModel, stopPolling]
  );

  const handleSignIn = useCallback(async () => {
    try {
      trackEvent(MixpanelEvent.Codex_SignIn_API_Call);
      onInputChangeRef.current("codex", "LLM");

      const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/initiate"), {
        method: "POST",
      });
      if (!res.ok) throw new Error("Failed to initiate auth");
      const { session_id: startedSessionId, url } = await res.json();

      setSessionId(startedSessionId);
      setAuthStatus("polling");
      window.open(url, "_blank", "noopener,noreferrer");

      pollIntervalRef.current = setInterval(async () => {
        try {
          const pollRes = await fetch(
            getApiUrl(`/api/v1/ppt/codex/auth/status/${startedSessionId}`)
          );
          if (!pollRes.ok) return;
          const pollData: StatusResponse = await pollRes.json();

          if (pollData.status === "success") {
            completeSignIn(pollData);
          } else if (pollData.status === "failed") {
            stopPolling();
            markSignedOut();
            notify.error(
              "로그인 실패",
              "인증이 완료되지 않았습니다. 다시 로그인해 주세요."
            );
          }
        } catch {
          // keep polling on transient errors
        }
      }, POLL_INTERVAL_MS);
    } catch {
      notify.error(
        "로그인 실패",
        "로그인 과정을 시작할 수 없습니다. 다시 시도해 주세요."
      );
      markSignedOut();
    }
  }, [completeSignIn, markSignedOut, stopPolling]);

  const handleManualExchange = useCallback(async () => {
    if (!sessionId || !manualCode.trim()) return;
    setIsExchanging(true);
    try {
      const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/exchange"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, code: manualCode.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Exchange failed");
      }
      completeSignIn(await res.json());
    } catch (err) {
      const message = err instanceof Error ? err.message : "";
      notify.error(
        "로그인 실패",
        message || "인증 코드를 처리할 수 없습니다. 다시 시도해 주세요."
      );
    } finally {
      setIsExchanging(false);
    }
  }, [completeSignIn, manualCode, sessionId]);

  const handleCancelPolling = useCallback(() => {
    stopPolling();
    setSessionId(null);
    setManualCode("");
    setAuthStatus("unauthenticated");
  }, [stopPolling]);

  const handleSignOut = useCallback(async () => {
    setIsLoggingOut(true);
    try {
      await fetch(getApiUrl("/api/v1/ppt/codex/auth/logout"), {
        method: "POST",
      });
      markSignedOut();
      // ChatGPT is unusable without a session, so the config falls back to the
      // documented default provider instead of keeping an unauthenticated one.
      onInputChangeRef.current("openai", "LLM");
      onInputChangeRef.current("", "codex_model");
      notify.success("로그아웃 완료", "ChatGPT 연결이 해제되었습니다.");
    } catch {
      notify.error(
        "로그아웃 실패",
        "ChatGPT 연결을 해제할 수 없습니다. 다시 시도해 주세요."
      );
    } finally {
      setIsLoggingOut(false);
    }
  }, [markSignedOut]);

  const handleRefreshToken = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/refresh"), {
        method: "POST",
      });
      if (!res.ok) throw new Error("Refresh failed");
      applyProfile(await res.json());
      notify.success(
        "세션 갱신 완료",
        "ChatGPT 연결이 성공적으로 갱신되었습니다."
      );
    } catch {
      notify.error(
        "세션 갱신 실패",
        "ChatGPT 세션을 갱신할 수 없습니다. 다시 로그인해 주세요."
      );
      markSignedOut();
    } finally {
      setIsRefreshing(false);
    }
  }, [applyProfile, markSignedOut]);

  return {
    authStatus,
    accountId,
    username,
    email,
    manualCode,
    setManualCode,
    isExchanging,
    isLoggingOut,
    isRefreshing,
    handleSignIn,
    handleManualExchange,
    handleCancelPolling,
    handleSignOut,
    handleRefreshToken,
  };
};
