"use client";
import { useEffect, useRef, useState } from "react";
import {
  Loader2,
  RefreshCw,
  Trash2,
  UserCheck,
  ArrowRight,
} from "lucide-react";
import { notify } from "@/components/ui/sonner";
import { getApiUrl } from "@/utils/api";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";

interface CodexConfigProps {
  codexModel: string;
  onInputChange: (value: string | boolean, field: string) => void;
}

type AuthStatus = "checking" | "unauthenticated" | "polling" | "authenticated";

interface StatusResponse {
  status: string;
  account_id?: string;
  username?: string;
  email?: string;
  is_pro?: boolean;
  detail?: string;
}

interface CodexModel {
  id: string;
  name: string;
}

export const CHATGPT_MODELS: CodexModel[] = [
  { id: "gpt-5.2", name: "GPT-5.2" },
  { id: "gpt-5.3-codex", name: "GPT-5.3 Codex" },
  { id: "gpt-5.3-codex-spark", name: "GPT-5.3 Codex Spark" },
  { id: "gpt-5.4", name: "GPT-5.4" },
  { id: "gpt-5.4-mini", name: "GPT-5.4 mini" },
  { id: "gpt-5.5", name: "GPT-5.5" },
];

export const DEFAULT_CODEX_MODEL = "gpt-5.2";

export default function CodexConfig({
  codexModel,
  onInputChange,
}: CodexConfigProps) {
  const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
  const [accountId, setAccountId] = useState<string | null>(null);
  const [username, setUsername] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [manualCode, setManualCode] = useState("");
  const [isExchanging, setIsExchanging] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  };

  useEffect(() => {
    checkCurrentAuthStatus();
    return () => stopPolling();
  }, []);

  const applyProfile = (data: Partial<StatusResponse>) => {
    setAccountId(data.account_id ?? null);
    setUsername(data.username ?? null);
    setEmail(data.email ?? null);
  };

  const checkCurrentAuthStatus = async () => {
    try {
      const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/status"));
      if (!res.ok) {
        setAuthStatus("unauthenticated");
        applyProfile({});
        return;
      }
      const data: StatusResponse = await res.json();
      if (data.status === "authenticated") {
        onInputChange('codex', 'LLM');
        onInputChange(DEFAULT_CODEX_MODEL, 'codex_model');
        setAuthStatus("authenticated");
        applyProfile(data);
      } else {
        setAuthStatus("unauthenticated");
        applyProfile({});
      }
    } catch {
      setAuthStatus("unauthenticated");
      applyProfile({});
    }
  };

  const handleSignIn = async () => {
    try {

      trackEvent(MixpanelEvent.Codex_SignIn_API_Call);
      onInputChange('codex', 'LLM');

      const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/initiate"), {
        method: "POST",
      });
      if (!res.ok) throw new Error("Failed to initiate auth");
      const data = await res.json();
      const { session_id, url } = data;

      setSessionId(session_id);
      setAuthStatus("polling");
      window.open(url, "_blank", "noopener,noreferrer");

      pollIntervalRef.current = setInterval(async () => {
        try {
          const pollRes = await fetch(
            getApiUrl(`/api/v1/ppt/codex/auth/status/${session_id}`)
          );
          if (!pollRes.ok) return;
          const pollData: StatusResponse = await pollRes.json();

          if (pollData.status === "success") {
            stopPolling();
            setAuthStatus("authenticated");
            applyProfile(pollData);
            setSessionId(null);
            if (!codexModel) {
              onInputChange(DEFAULT_CODEX_MODEL, "codex_model");
            }
            notify.success(
              "ChatGPT 로그인 완료",
              "ChatGPT 계정이 연결되어 사용할 준비가 되었습니다."
            );
          } else if (pollData.status === "failed") {
            stopPolling();
            setAuthStatus("unauthenticated");
            applyProfile({});
            notify.error(
              "로그인 실패",
              "인증이 완료되지 않았습니다. 다시 로그인해 주세요."
            );
          }
        } catch {
          // keep polling on transient errors
        }
      }, 2000);
    } catch (err) {
      notify.error(
        "로그인 실패",
        "로그인 과정을 시작할 수 없습니다. 다시 시도해 주세요."
      );
      setAuthStatus("unauthenticated");
      applyProfile({});
    }
  };

  const handleManualExchange = async () => {
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
      const data = await res.json();
      stopPolling();
      setAuthStatus("authenticated");
      applyProfile(data);
      setSessionId(null);
      setManualCode("");
      if (!codexModel) {
        onInputChange(DEFAULT_CODEX_MODEL, "codex_model");
      }
      notify.success(
        "ChatGPT 로그인 완료",
        "ChatGPT 계정이 연결되어 사용할 준비가 되었습니다."
      );
    } catch (err: any) {
      notify.error(
        "로그인 실패",
        err.message || "인증 코드를 처리할 수 없습니다. 다시 시도해 주세요."
      );
    } finally {
      setIsExchanging(false);
    }
  };

  const handleCancelPolling = () => {
    stopPolling();
    setSessionId(null);
    setManualCode("");
    setAuthStatus("unauthenticated");
  };

  const handleSignOut = async () => {
    setIsLoggingOut(true);
    try {
      await fetch(getApiUrl("/api/v1/ppt/codex/auth/logout"), { method: "POST" });
      setAuthStatus("unauthenticated");
      setAccountId(null);
      setUsername(null);
      setEmail(null);
      onInputChange("openai", "LLM");
      onInputChange("", "codex_model");
      notify.success(
        "로그아웃 완료",
        "ChatGPT 연결이 해제되었습니다."
      );
    } catch {
      notify.error(
        "로그아웃 실패",
        "ChatGPT 연결을 해제할 수 없습니다. 다시 시도해 주세요."
      );
    } finally {
      setIsLoggingOut(false);
    }
  };

  const handleRefreshToken = async () => {
    setIsRefreshing(true);
    try {
      const res = await fetch(getApiUrl("/api/v1/ppt/codex/auth/refresh"), {
        method: "POST",
      });
      if (!res.ok) throw new Error("Refresh failed");
      const data = await res.json();
      applyProfile(data);
      notify.success(
        "세션 갱신 완료",
        "ChatGPT 연결이 성공적으로 갱신되었습니다."
      );
    } catch {
      notify.error(
        "세션 갱신 실패",
        "ChatGPT 세션을 갱신할 수 없습니다. 다시 로그인해 주세요."
      );
      setAuthStatus("unauthenticated");
      applyProfile({});
    } finally {
      setIsRefreshing(false);
    }
  };

  if (authStatus === "checking") {
    return (
      <div className="mb-5 w-full p-3 border border-[#EDEEEF] font-syne rounded-[8px] flex items-center gap-6">
        <div className="w-[74px] h-[74px] bg-[#333333] rounded-full flex items-center justify-center shrink-0">
          <Loader2 className="w-10 h-10 text-[#191919] animate-spin" />
        </div>
        <div className="text-start flex-1 min-w-0">
          <h4 className="text-[#191919] text-lg font-medium">상태 확인 중</h4>
          <p className="text-[#B3B3B3] text-sm font-normal">
            ChatGPT 연결을 확인하는 중…
          </p>
        </div>
      </div>
    );
  }

  if (authStatus === "polling") {
    return (
      <div className="mb-5 space-y-4 font-syne">
        <div className="w-full p-3 border border-[#EDEEEF] rounded-[8px] flex items-center justify-between gap-4">
          <div className="flex items-center gap-6 min-w-0 flex-1">
            <div className="w-[40px] h-[40px] bg-[#EDEEEF] rounded-full flex items-center justify-center shrink-0">
              <Loader2 className="w-5 h-5 text-[#191919] animate-spin" />
            </div>
            <div className="text-start min-w-0">
              <h4 className="text-[#191919] text-lg font-medium">로그인 대기 중</h4>
              <p className="text-[#B3B3B3] text-sm font-normal">
                새로 열린 브라우저 탭에서 로그인을 완료해 주세요.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleCancelPolling}
            className="shrink-0 text-sm text-[#B3B3B3] hover:text-[#191919] underline underline-offset-2 transition-colors"
          >
            취소
          </button>
        </div>

        <div className="space-y-2 rounded-[8px] border border-[#EDEEEF] p-3">
          <p className="text-[#191919] text-xs font-normal">
            자동으로 이동되지 않았다면 리디렉션 URL 또는 코드를 붙여넣으세요
          </p>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="URL 또는 코드 붙여넣기…"
              className="flex-1 min-w-0 px-3 py-2.5 outline-none border border-[#EDEEEF] rounded-[8px]  text-sm text-[#191919] placeholder:text-[#666666] focus:border-[#555555] transition-colors"
              value={manualCode}
              onChange={(e) => setManualCode(e.target.value)}
            />
            <button
              type="button"
              onClick={handleManualExchange}
              disabled={isExchanging || !manualCode.trim()}
              className="shrink-0 px-4 py-2.5 bg-[#EDEEEF] hover:bg-[#E4E5E6] disabled:opacity-40 disabled:hover:bg-[#EDEEEF] rounded-[8px] text-sm font-medium text-[#191919] transition-colors flex items-center justify-center min-w-[88px]"
            >
              {isExchanging ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                "제출"
              )}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (authStatus === "authenticated") {

    return (
      <div className=" mb-5">
        <div className="flex items-center justify-between gap-3 p-5  border border-[#EDEEEF] rounded-[8px]">
          <div className="flex items-center gap-3">

            <div className="w-[40px] h-[40px] bg-[#333333] rounded-full flex items-center justify-center" >

              <img src="/providers/OpenAI-white.png" alt="openai Logo" className="w-[27px] h-[27px]" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 min-w-0">
                <p className="text-sm font-medium text-[#191919] truncate">
                  {username || email || (accountId ? `계정 ${accountId}` : "ChatGPT 계정")}
                </p>

              </div>
              {email && username && (
                <p className="text-xs text-[#B3B3B3] truncate">{email}</p>
              )}
              {!email && accountId && (
                <p className="text-xs text-[#B3B3B3] truncate">ID: {accountId}</p>
              )}
              <p className="text-xs text-[#B3B3B3]">ChatGPT에 로그인됨</p>
            </div>
          </div>
          <div className="flex gap-1.5 shrink-0">
            <button
              onClick={handleRefreshToken}
              disabled={isRefreshing}
              title="토큰 갱신"
              className="flex items-center justify-center px-3.5 py-2.5  border border-[#EDEEEF] rounded-[58px] minid:opacity-40 transition-colors"
            >
              {isRefreshing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-[#191919]" />
              ) : (
                <RefreshCw className="w-3.5 h-3.5 text-[#191919]" />
              )}
            </button>
            <button
              onClick={handleSignOut}
              disabled={isLoggingOut}
              title="로그아웃"
              className="flex items-center justify-center px-3.5 py-2.5  border border-[#EDEEEF] rounded-[58px]  disabled:opacity-40 transition-colors"
            >
              {isLoggingOut ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-[#191919]" />
              ) : (
                <Trash2 className="w-3.5 h-3.5 text-[#191919]" />
              )}
            </button>
          </div>
        </div>


      </div>
    );
  }

  return (
    <button
      onClick={handleSignIn}
      className=" w-full  p-5 border border-[#EDEEEF] font-syne  hover:bg-[#F7F6F9] transition-colors duration-300   rounded-[12px] flex items-center   justify-between  "
    >
      <div className="flex items-center gap-2 flex-1">
        <div className="w-[40px] h-[40px] bg-[#333333] rounded-full flex items-center justify-center" >

          <img src="/providers/OpenAI-white.png" alt="openai Logo" className="w-[27px] h-[27px]" />
        </div>
        <div className="text-start flex-1">
          <h4 className="text-[#191919] text-sm font-medium">ChatGPT로 로그인</h4>
          <p className="text-[#B3B3B3]   text-xs font-normal">ChatGPT 계정을 사용하세요 — API 키가 필요 없습니다</p>
        </div>
      </div>
      <ArrowRight className="w-[22px] h-[22px] text-[#4C4C4C]" />
    </button>
  );
}
