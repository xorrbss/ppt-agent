"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Image from "next/image";
import { getApiUrl } from "@/utils/api";
import { formatFastApiDetail, UNAUTHORIZED_DETAIL } from "@/utils/authErrors";
import { notify } from "@/components/ui/sonner";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";

const PRESENTON_SPLASH_MIN_DURATION_MS = 3000;

type AuthStatus = {
  configured: boolean;
  authenticated: boolean;
  username: string | null;
  role?: "admin" | "user" | null;
};

const initialStatus: AuthStatus = {
  configured: false,
  authenticated: false,
  username: null,
  role: null,
};

export default function AuthGate() {
  const [status, setStatus] = useState<AuthStatus>(initialStatus);
  const [isLoading, setIsLoading] = useState(true);
  const [isRedirecting, setIsRedirecting] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [hasMetSplashDuration, setHasMetSplashDuration] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const isSetupMode = useMemo(() => !status.configured, [status.configured]);

  useEffect(() => {
    const timeout = window.setTimeout(
      () => setHasMetSplashDuration(true),
      PRESENTON_SPLASH_MIN_DURATION_MS
    );
    return () => window.clearTimeout(timeout);
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, []);

  useEffect(() => {
    if (
      typeof window === "undefined" ||
      isLoading ||
      !status.authenticated ||
      isRedirecting
    ) {
      return;
    }

    setIsRedirecting(true);
    window.location.replace("/upload");
  }, [isLoading, isRedirecting, status.authenticated]);

  useEffect(() => {
    if (typeof window === "undefined" || isLoading) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    if (params.get("reason") === "unauthorized") {
      if (status.configured && !status.authenticated) {
        notify.error("권한 없음", "이 페이지를 보려면 로그인하세요.", {
          id: "auth-unauthorized-redirect",
          duration: 5000,
        });
      }
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [isLoading, status.authenticated, status.configured]);

  const refreshStatus = async () => {
    setIsLoading(true);

    try {
      const response = await fetch(getApiUrl("/api/v1/auth/status"), {
        method: "GET",
        cache: "no-store",
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("Could not load login state");
      }

      const data = (await response.json()) as AuthStatus;
      setStatus({
        configured: Boolean(data.configured),
        authenticated: Boolean(data.authenticated),
        username: data.username ?? null,
        role: data.role ?? null,
      });
    } catch (fetchError) {
      console.error(fetchError);
      notify.error(
        "로그인을 불러올 수 없음",
        "로그인 서비스에 연결할 수 없습니다. 페이지를 새로고침한 후 다시 시도하세요."
      );
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (
      isLoading ||
      isRedirecting ||
      status.authenticated ||
      !hasMetSplashDuration
    ) {
      return;
    }

    trackEvent(MixpanelEvent.Auth_Gate_Viewed, {
      flow: status.configured ? "sign_in" : "setup",
    });
  }, [
    hasMetSplashDuration,
    isLoading,
    isRedirecting,
    status.authenticated,
    status.configured,
  ]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const cleanedUsername = username.trim();
    if (cleanedUsername.length < 3) {
      trackEvent(MixpanelEvent.Auth_Validation_Failed, {
        flow: isSetupMode ? "setup" : "sign_in",
        reason: "username_too_short",
      });
      notify.warning(
        "사용자 이름이 너무 짧음",
        "사용자 이름은 3자 이상이어야 합니다."
      );
      return;
    }

    const minimumPasswordLength = isSetupMode ? 8 : 6;
    if (password.length < minimumPasswordLength) {
      trackEvent(MixpanelEvent.Auth_Validation_Failed, {
        flow: isSetupMode ? "setup" : "sign_in",
        reason: "password_too_short",
      });
      notify.warning(
        "비밀번호가 너무 짧음",
        `비밀번호는 ${minimumPasswordLength}자 이상이어야 합니다.`
      );
      return;
    }

    if (isSetupMode && password !== confirmPassword) {
      trackEvent(MixpanelEvent.Auth_Validation_Failed, {
        flow: "setup",
        reason: "passwords_do_not_match",
      });
      notify.warning(
        "비밀번호가 일치하지 않음",
        "계속하기 전에 두 비밀번호가 일치하는지 확인하세요."
      );
      return;
    }

    setIsSubmitting(true);

    try {
      const response = await fetch(
        getApiUrl(isSetupMode ? "/api/v1/auth/setup" : "/api/v1/auth/login"),
        {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            username: cleanedUsername,
            password,
          }),
        }
      );

      const payload = await response.json();
      if (!response.ok) {
        const detail = formatFastApiDetail(payload?.detail);
        if (response.status === 401) {
          notify.error(
            "로그인 실패",
            detail === UNAUTHORIZED_DETAIL
              ? "사용자 이름 또는 비밀번호가 올바르지 않습니다. 다시 시도하세요."
              : detail
          );
        } else {
          notify.error(
            isSetupMode ? "계정을 만들 수 없음" : "로그인 실패",
            detail || "문제가 발생했습니다. 다시 시도하세요."
          );
        }
        return;
      }

      if (isSetupMode) {
        setStatus({
          configured: true,
          authenticated: false,
          username: (payload as AuthStatus).username ?? cleanedUsername,
          role: (payload as AuthStatus).role ?? "admin",
        });
        setPassword("");
        setConfirmPassword("");
        notify.success("계정 생성됨", "새로 만든 사용자 이름과 비밀번호로 로그인하여 계속하세요.", {
          duration: 6000,
        });
        return;
      }

      setStatus({
        configured: Boolean((payload as AuthStatus).configured),
        authenticated: Boolean((payload as AuthStatus).authenticated),
        username: (payload as AuthStatus).username ?? cleanedUsername,
        role: (payload as AuthStatus).role ?? null,
      });
      setPassword("");
      setConfirmPassword("");
      notify.success(
        "로그인됨",
        "다시 오신 것을 환영합니다. 작업 공간을 불러오는 중입니다."
      );
    } catch (submitError) {
      console.error(submitError);
      notify.error(
        "로그인을 사용할 수 없음",
        "현재 로그인 서비스를 사용할 수 없습니다. 잠시 후 다시 시도하세요."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading || isRedirecting || status.authenticated) {
    return (
      <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-white p-6">
        <div className="relative z-10 w-full max-w-md">
          <div className="rounded-2xl border border-[#EDEEEF] bg-white p-8 text-center shadow-xl">
            <Image
              src="/Logo.png"
              alt="Presenton"
              width={160}
              height={48}
              className="mx-auto mb-5 h-12 w-auto opacity-95"
              priority
            />
            <div className="mx-auto mb-4 h-1 w-16 rounded-full bg-[#7C51F8]" />
            <h1 className="font-syne text-lg font-semibold text-black">Presenton</h1>
            <p className="mt-3 font-syne text-sm text-[#000000CC]">작업 공간을 준비하는 중…</p>
            <div className="mt-6 flex justify-center gap-1.5">
              <span className="h-2 w-2 animate-pulse rounded-full bg-[#5146E5]" />
              <span
                className="h-2 w-2 animate-pulse rounded-full bg-[#7C51F8]"
                style={{ animationDelay: "0.2s" }}
              />
              <span
                className="h-2 w-2 animate-pulse rounded-full bg-[#5146E5]"
                style={{ animationDelay: "0.4s" }}
              />
            </div>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-white p-6 font-syne">
      <section className="relative z-10 w-full max-w-lg rounded-[20px] border border-[#EDEEEF] bg-[#F9F8F8] p-7 sm:p-10">
        <div className="mb-7">
          <div className="flex items-center gap-4">
            <div className="flex h-[60px] w-[60px] shrink-0 items-center justify-center rounded-[4px] bg-[#F4F3FF] p-3">
              <Image
                src="/logo-with-bg.png"
                alt=""
                width={161}
                height={166}
                className="h-10 w-auto object-contain"
              />
            </div>
            <div>
              <p className="font-syne text-[10px] font-semibold uppercase tracking-[0.14em] text-[#7A5AF8]">
                보안 인스턴스
              </p>
              <h1 className="mt-1 font-syne text-2xl font-semibold leading-tight text-black sm:text-[26px]">
                {isSetupMode ? "관리자 로그인 만들기" : "계속하려면 로그인하세요"}
              </h1>
            </div>
          </div>
        </div>

        <p className="max-w-md text-sm leading-relaxed text-[#6B7280]">
          {isSetupMode
            ? "이 배포에 대한 1회성 설정입니다. 이후 방문 시에도 동일한 사용자 이름과 비밀번호를 사용합니다."
            : "이 배포는 보호되어 있습니다. 앱을 열려면 자격 증명을 입력하세요."}
        </p>

        <form onSubmit={handleSubmit} className="mt-7 space-y-5">
          <div className="space-y-2">
            <label htmlFor="username" className="block font-syne text-sm font-medium text-black">
              사용자 이름
            </label>
            <input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value.replace(/\s/g, ""))}
              minLength={3}
              maxLength={128}
              pattern="\S+"
              title="사용자 이름에는 공백을 사용할 수 없습니다."
              required
              spellCheck={false}
              placeholder="관리자 사용자 이름"
              className="w-full rounded-[11px] border border-[#EDEEEF] bg-white px-4 py-3 font-syne text-sm text-black outline-none transition placeholder:text-[#999999] focus:border-[#a49cfc] focus:ring-2 focus:ring-[#5146E5]/20"
              disabled={isSubmitting}
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="password" className="block font-syne text-sm font-medium text-black">
              비밀번호
            </label>
            <input
              id="password"
              type="password"
              autoComplete={isSetupMode ? "new-password" : "current-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={isSetupMode ? 8 : 6}
              maxLength={128}
              required
              placeholder={isSetupMode ? "8자 이상" : "6자 이상"}
              className="w-full rounded-[11px] border border-[#EDEEEF] bg-white px-4 py-3 font-syne text-sm text-black outline-none transition placeholder:text-[#999999] focus:border-[#a49cfc] focus:ring-2 focus:ring-[#5146E5]/20"
              disabled={isSubmitting}
            />
          </div>

          {isSetupMode ? (
            <div className="space-y-2">
              <label htmlFor="confirmPassword" className="block font-syne text-sm font-medium text-black">
                비밀번호 확인
              </label>
              <input
                id="confirmPassword"
                type="password"
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                minLength={8}
                maxLength={128}
                required
                placeholder="비밀번호를 다시 입력하세요"
                className="w-full rounded-[11px] border border-[#EDEEEF] bg-white px-4 py-3 font-syne text-sm text-black outline-none transition placeholder:text-[#999999] focus:border-[#a49cfc] focus:ring-2 focus:ring-[#5146E5]/20"
                disabled={isSubmitting}
              />
            </div>
          ) : null}

          {!isSetupMode && status.configured ? (
            <p className="font-syne text-sm text-[#494A4D]">
              이 인스턴스의 설정이 완료되었습니다. 설정한 사용자 이름과 비밀번호를 사용하세요.
            </p>
          ) : null}

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full rounded-[58px] border border-[#EDEEEF] bg-[#7C51F8] px-5 py-3 font-syne text-xs font-semibold text-white transition hover:bg-[#6d46e6] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting
              ? isSetupMode
                ? "자격 증명 저장 중…"
                : "로그인 중…"
              : isSetupMode
                ? "계정 만들기"
                : "로그인"}
          </button>
        </form>
      </section>
    </main>
  );
}
