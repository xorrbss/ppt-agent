"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Check, Copy, Eye, Link2, Loader2, RotateCw } from "lucide-react";

import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import type { ShareInfo } from "@/app/(presentation-generator)/services/api/dashboard";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { notify } from "@/components/ui/sonner";

type ExpirationChoice = "never" | "1" | "7" | "30";

const expirationDays: Record<Exclude<ExpirationChoice, "never">, number> = {
  "1": 1,
  "7": 7,
  "30": 30,
};

function choiceForStatus(status: ShareInfo): ExpirationChoice {
  if (!status.expires_at) return "never";
  const remainingDays = Math.ceil(
    (new Date(status.expires_at).getTime() - Date.now()) / 86_400_000,
  );
  if (remainingDays <= 1) return "1";
  if (remainingDays <= 7) return "7";
  return "30";
}

function expirationForChoice(choice: ExpirationChoice): string | null {
  if (choice === "never") return null;
  return new Date(Date.now() + expirationDays[choice] * 86_400_000).toISOString();
}

export function SharePopover({
  presentationId,
  title,
  open,
  onOpenChange,
}: {
  presentationId: string;
  title: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [status, setStatus] = useState<ShareInfo | null>(null);
  const [expiration, setExpiration] = useState<ExpirationChoice>("7");
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [action, setAction] = useState<"save" | "rotate" | "disable" | null>(
    null,
  );
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setStatus(null);
    setLoadError(false);
    setIsLoading(true);
    setCopied(false);
    void DashboardApi.getShareStatus(presentationId)
      .then((nextStatus) => {
        if (cancelled) return;
        setStatus(nextStatus);
        setExpiration(choiceForStatus(nextStatus));
      })
      .catch(() => {
        if (!cancelled) {
          setLoadError(true);
          notify.error("공유 설정을 불러오지 못했습니다.");
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadAttempt, open, presentationId]);

  const shareUrl = useMemo(() => {
    if (!status?.share_token || typeof window === "undefined") return "";
    return `${window.location.origin}/p/${encodeURIComponent(status.share_token)}`;
  }, [status?.share_token]);

  const save = async (regenerate: boolean) => {
    setAction(regenerate ? "rotate" : "save");
    try {
      const nextStatus = await DashboardApi.enableShare(
        presentationId,
        expirationForChoice(expiration),
        regenerate,
      );
      setStatus(nextStatus);
      setCopied(false);
      notify.success(
        regenerate ? "공유 링크를 새로 만들었습니다." : "공유 설정을 저장했습니다.",
      );
    } catch {
      notify.error("공유 설정을 저장하지 못했습니다.");
    } finally {
      setAction(null);
    }
  };

  const disable = async () => {
    setAction("disable");
    try {
      const nextStatus = await DashboardApi.disableShare(presentationId);
      setStatus(nextStatus);
      setCopied(false);
      notify.success("공유를 해제했습니다.");
    } catch {
      notify.error("공유를 해제하지 못했습니다.");
    } finally {
      setAction(null);
    }
  };

  const copy = async () => {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    notify.success("공유 링크를 복사했습니다.");
  };

  const busy = action !== null;
  const hasActiveLink = Boolean(status?.shared && !status.expired);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-[520px] gap-0 overflow-hidden rounded-2xl border-0 bg-white p-0"
        overlayClassName="bg-black/45 backdrop-blur-[2px]"
        onClick={(event) => event.stopPropagation()}
      >
        <DialogHeader className="border-b border-[#EDEEEF] px-6 py-5">
          <DialogTitle className="flex items-center gap-2 text-[#101828]">
            <Link2 className="h-5 w-5 text-[#5146E5]" />
            발표자료 공유
          </DialogTitle>
          <DialogDescription className="truncate pr-8">
            {title || "제목 없는 발표자료"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5 px-6 py-5">
          {isLoading ? (
            <div className="flex min-h-[220px] items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-[#7A5AF8]" />
            </div>
          ) : loadError || !status ? (
            <div className="flex min-h-[220px] flex-col items-center justify-center gap-3 text-center">
              <p className="text-sm text-[#667085]">
                공유 설정을 불러오지 못했습니다.
              </p>
              <button
                type="button"
                onClick={() => setLoadAttempt((current) => current + 1)}
                className="h-9 rounded-lg border border-[#D9D9DF] px-3 text-sm font-medium text-[#344054] hover:bg-[#F8F9FB]"
              >
                다시 시도
              </button>
            </div>
          ) : (
            <>
              {status?.expired && (
                <div
                  role="status"
                  className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
                >
                  이 공유 링크는 만료되었습니다. 새 링크를 만들면 이전 링크는
                  다시 사용할 수 없습니다.
                </div>
              )}

              {hasActiveLink && shareUrl && (
                <div className="space-y-2">
                  <label className="text-xs font-medium text-[#667085]">
                    공유 링크
                  </label>
                  <div className="flex gap-2">
                    <input
                      readOnly
                      value={shareUrl}
                      aria-label="공유 링크"
                      className="h-10 min-w-0 flex-1 rounded-lg border border-[#D9D9DF] bg-[#F8F9FB] px-3 text-sm text-[#475467]"
                    />
                    <button
                      type="button"
                      onClick={() => void copy()}
                      className="flex h-10 items-center gap-2 rounded-lg bg-[#5146E5] px-3 text-sm font-medium text-white hover:bg-[#4338CA]"
                    >
                      {copied ? (
                        <Check className="h-4 w-4" />
                      ) : (
                        <Copy className="h-4 w-4" />
                      )}
                      {copied ? "복사됨" : "복사"}
                    </button>
                  </div>
                </div>
              )}

              <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
                <label className="space-y-2">
                  <span className="block text-xs font-medium text-[#667085]">
                    링크 만료
                  </span>
                  <select
                    value={expiration}
                    onChange={(event) =>
                      setExpiration(event.target.value as ExpirationChoice)
                    }
                    className="h-10 w-full rounded-lg border border-[#D9D9DF] bg-white px-3 text-sm text-[#344054] outline-none focus:border-[#7A5AF8] focus:ring-2 focus:ring-[#7A5AF8]/20"
                  >
                    <option value="never">만료 없음</option>
                    <option value="1">1일 후</option>
                    <option value="7">7일 후</option>
                    <option value="30">30일 후</option>
                  </select>
                </label>
                <div className="flex h-10 items-center gap-2 rounded-lg bg-[#F2F4F7] px-3 text-sm text-[#475467]">
                  <Eye className="h-4 w-4" />
                  조회 {status?.view_count ?? 0}회
                </div>
              </div>

              {status?.expires_at && hasActiveLink && (
                <p className="text-xs text-[#667085]">
                  {new Date(status.expires_at).toLocaleString()}에 만료됩니다.
                </p>
              )}

              <div className="flex flex-col-reverse gap-2 border-t border-[#EDEEEF] pt-4 sm:flex-row sm:justify-between">
                <div>
                  {hasActiveLink && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void disable()}
                      className="h-10 rounded-lg px-3 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
                    >
                      {action === "disable" ? "해제 중…" : "공유 해제"}
                    </button>
                  )}
                </div>
                <div className="flex flex-col-reverse gap-2 sm:flex-row">
                  {hasActiveLink && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void save(true)}
                      className="flex h-10 items-center justify-center gap-2 rounded-lg border border-[#D9D9DF] px-3 text-sm font-medium text-[#344054] hover:bg-[#F8F9FB] disabled:opacity-50"
                    >
                      <RotateCw className="h-4 w-4" />
                      {action === "rotate" ? "생성 중…" : "링크 재생성"}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void save(false)}
                    className="h-10 rounded-lg bg-[#5146E5] px-4 text-sm font-medium text-white hover:bg-[#4338CA] disabled:opacity-50"
                  >
                    {action === "save"
                      ? "저장 중…"
                      : hasActiveLink
                        ? "설정 저장"
                        : status?.expired
                          ? "새 링크 만들기"
                          : "공유 링크 만들기"}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
