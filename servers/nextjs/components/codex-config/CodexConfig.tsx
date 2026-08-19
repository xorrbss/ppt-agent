"use client";
import Image from "next/image";
import { ArrowRight, Loader2, RefreshCw, Trash2 } from "lucide-react";
import CodexModelSelect from "./CodexModelSelect";
import { useCodexAuth } from "./useCodexAuth";

export type CodexConfigVariant = "onboarding" | "settings";

export interface CodexConfigProps {
  codexModel: string;
  onInputChange: (value: string | boolean, field: string) => void;
  /**
   * "onboarding" is the wide first-run card: it adopts ChatGPT as the active
   * provider once a session exists and leaves model selection to the page.
   * "settings" is the narrow provider column and owns the model dropdown.
   */
  variant?: CodexConfigVariant;
}

export default function CodexConfig({
  codexModel,
  onInputChange,
  variant = "onboarding",
}: CodexConfigProps) {
  const dense = variant === "settings";
  const {
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
  } = useCodexAuth({
    codexModel,
    onInputChange,
    adoptProviderOnSignedIn: !dense,
  });

  const rootClass = dense ? "space-y-4" : "mb-5 space-y-4 font-syne";
  const badgeClass = `flex shrink-0 items-center justify-center rounded-full ${dense ? "h-8 w-8" : "h-10 w-10"
    }`;
  const titleClass = `font-medium text-[#191919] ${dense ? "text-sm" : "text-lg"}`;
  const iconButtonClass = `flex items-center justify-center rounded-full border border-[#EDEEEF] transition-colors disabled:opacity-40 ${dense ? "h-8 w-8 bg-[#EDEEEF] hover:bg-[#E4E5E6]" : "px-3.5 py-2.5"
    }`;

  const openAiBadge = (
    <div className={`${badgeClass} bg-[#333333]`}>
      <Image
        src="/providers/OpenAI-white.png"
        alt="OpenAI logo"
        width={27}
        height={27}
        className={dense ? "h-[18px] w-[18px]" : "h-[27px] w-[27px]"}
      />
    </div>
  );

  const spinnerBadge = (
    <div className={`${badgeClass} bg-[#EDEEEF]`}>
      <Loader2
        className={`animate-spin text-[#191919] ${dense ? "h-4 w-4" : "h-5 w-5"}`}
      />
    </div>
  );

  if (authStatus === "checking") {
    return (
      <div
        className={`flex items-center gap-3 rounded-[8px] border border-[#EDEEEF] p-3 ${dense ? "" : "mb-5 font-syne"
          }`}
      >
        {spinnerBadge}
        <div className="min-w-0 text-start">
          <h4 className={titleClass}>상태 확인 중</h4>
          {!dense && (
            <p className="text-sm font-normal text-[#B3B3B3]">
              ChatGPT 연결을 확인하는 중…
            </p>
          )}
        </div>
      </div>
    );
  }

  if (authStatus === "polling") {
    return (
      <div className={rootClass}>
        <div className="flex items-center justify-between gap-4 rounded-[8px] border border-[#EDEEEF] p-3">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            {spinnerBadge}
            <div className="min-w-0 text-start">
              <h4 className={titleClass}>로그인 대기 중</h4>
              {!dense && (
                <p className="text-sm font-normal text-[#B3B3B3]">
                  새로 열린 브라우저 탭에서 로그인을 완료해 주세요.
                </p>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={handleCancelPolling}
            className="shrink-0 text-xs text-[#B3B3B3] underline underline-offset-2 transition-colors hover:text-[#191919]"
          >
            취소
          </button>
        </div>

        <div className="space-y-2 rounded-[8px] border border-[#EDEEEF] p-3">
          <p className="text-xs font-normal text-[#666666]">
            자동으로 이동되지 않았다면 리디렉션 URL 또는 코드를 붙여넣으세요
          </p>
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="URL 또는 코드 붙여넣기…"
              className="min-w-0 flex-1 rounded-[8px] border border-[#EDEEEF] px-3 py-2.5 text-sm text-[#191919] outline-none transition-colors placeholder:text-[#666666] focus:border-[#555555]"
              value={manualCode}
              onChange={(event) => setManualCode(event.target.value)}
            />
            <button
              type="button"
              onClick={handleManualExchange}
              disabled={isExchanging || !manualCode.trim()}
              className="flex shrink-0 items-center justify-center rounded-[8px] bg-[#EDEEEF] px-4 py-2.5 text-sm font-medium text-[#191919] transition-colors hover:bg-[#E4E5E6] disabled:opacity-40 disabled:hover:bg-[#EDEEEF]"
            >
              {isExchanging ? (
                <Loader2 className="h-4 w-4 animate-spin" />
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
      <div className={rootClass}>
        <div
          className={`flex items-center justify-between gap-3 rounded-[8px] border border-[#EDEEEF] ${dense ? "p-3" : "p-5"
            }`}
        >
          <div className="flex min-w-0 flex-1 items-center gap-3">
            {openAiBadge}
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-[#191919]">
                {username ||
                  email ||
                  (accountId ? `계정 ${accountId}` : "ChatGPT 계정")}
              </p>
              {email && username && (
                <p className="truncate text-xs text-[#B3B3B3]">{email}</p>
              )}
              {!email && accountId && (
                <p className="truncate text-xs text-[#B3B3B3]">ID: {accountId}</p>
              )}
              <p className="text-xs text-[#B3B3B3]">ChatGPT에 로그인됨</p>
            </div>
          </div>
          <div className="flex shrink-0 gap-1.5">
            <button
              type="button"
              onClick={handleRefreshToken}
              disabled={isRefreshing}
              title="토큰 갱신"
              className={iconButtonClass}
            >
              {isRefreshing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-[#191919]" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5 text-[#191919]" />
              )}
            </button>
            <button
              type="button"
              onClick={handleSignOut}
              disabled={isLoggingOut}
              title="로그아웃"
              className={iconButtonClass}
            >
              {isLoggingOut ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-[#191919]" />
              ) : (
                <Trash2 className="h-3.5 w-3.5 text-[#191919]" />
              )}
            </button>
          </div>
        </div>

        {dense && (
          <CodexModelSelect
            codexModel={codexModel}
            onSelect={(modelId) => onInputChange(modelId, "codex_model")}
          />
        )}
      </div>
    );
  }

  if (dense) {
    return (
      <button
        type="button"
        onClick={handleSignIn}
        className="w-fit rounded-[48px] bg-[#EDEEEF] px-3.5 py-2.5 text-xs font-semibold text-[#101323] transition-colors hover:bg-[#E4E5E6]"
      >
        ChatGPT로 로그인
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={handleSignIn}
      className="mb-5 flex w-full items-center justify-between rounded-[12px] border border-[#EDEEEF] p-5 font-syne transition-colors duration-300 hover:bg-[#F7F6F9]"
    >
      <div className="flex flex-1 items-center gap-2">
        {openAiBadge}
        <div className="flex-1 text-start">
          <h4 className="text-sm font-medium text-[#191919]">ChatGPT로 로그인</h4>
          <p className="text-xs font-normal text-[#B3B3B3]">
            ChatGPT 계정을 사용하세요 — API 키가 필요 없습니다
          </p>
        </div>
      </div>
      <ArrowRight className="h-[22px] w-[22px] text-[#4C4C4C]" />
    </button>
  );
}
