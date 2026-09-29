"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import { notify } from "@/components/ui/sonner";
import { codexAuthStatus } from "@/app/(presentation-generator)/services/api/codex-auth";
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
  const t = useTranslations("a2cCodexConfig");
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
      const data = await codexAuthStatus<StatusResponse>();
      if (!data || data.status !== "authenticated") {
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
        t("signInComplete"),
        t("signInReady")
      );
    },
    [applyProfile, seedDefaultModel, stopPolling, t]
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
              t("signInFailed"),
              t("authIncomplete")
            );
          }
        } catch {
          // keep polling on transient errors
        }
      }, POLL_INTERVAL_MS);
    } catch {
      notify.error(
        t("signInFailed"),
        t("cannotStartSignIn")
      );
      markSignedOut();
    }
  }, [completeSignIn, markSignedOut, stopPolling, t]);

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
        t("signInFailed"),
        message || "인증 코드를 처리할 수 없습니다. 다시 시도해 주세요."
      );
    } finally {
      setIsExchanging(false);
    }
  }, [completeSignIn, manualCode, sessionId, t]);

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
      notify.success(t("signOutComplete"), t("disconnected"));
    } catch {
      notify.error(
        t("signOutFailed"),
        t("cannotDisconnect")
      );
    } finally {
      setIsLoggingOut(false);
    }
  }, [markSignedOut, t]);

  const handleRefreshToken = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/refresh"), {
        method: "POST",
      });
      if (!res.ok) throw new Error("Refresh failed");
      applyProfile(await res.json());
      notify.success(
        t("refreshComplete"),
        t("refreshed")
      );
    } catch {
      notify.error(
        t("refreshFailed"),
        t("cannotRefresh")
      );
      markSignedOut();
    } finally {
      setIsRefreshing(false);
    }
  }, [applyProfile, markSignedOut, t]);

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
