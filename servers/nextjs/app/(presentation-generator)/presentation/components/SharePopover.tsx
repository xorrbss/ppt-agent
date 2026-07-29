"use client";
import React, { useState } from "react";
import { Check, Copy, Link2, Loader2, RefreshCw, Share2 } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { notify } from "@/components/ui/sonner";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";

const SharePopover = ({
  presentationId,
  disabled,
}: {
  presentationId: string;
  disabled?: boolean;
}) => {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [shared, setShared] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const shareUrl =
    shared && token && typeof window !== "undefined"
      ? `${window.location.origin}/p/${token}`
      : "";

  const loadStatus = async () => {
    setLoading(true);
    try {
      const info = await PresentationGenerationApi.getShareStatus(presentationId);
      setShared(Boolean(info?.shared));
      setToken(info?.share_token ?? null);
    } catch {
      notify.error("공유 상태를 불러오지 못했습니다", "다시 시도해 주세요.");
    } finally {
      setLoading(false);
    }
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    setCopied(false);
    if (next) loadStatus();
  };

  const handleEnable = async (regenerate = false) => {
    setBusy(true);
    try {
      const info = await PresentationGenerationApi.enableShare(
        presentationId,
        regenerate
      );
      setShared(true);
      setToken(info?.share_token ?? null);
      notify.success(
        regenerate ? "링크를 재발급했습니다" : "공유를 켰습니다",
        regenerate ? "이전 링크는 더 이상 열리지 않습니다." : undefined
      );
    } catch {
      notify.error("공유를 켜지 못했습니다", "다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  };

  const handleDisable = async () => {
    setBusy(true);
    try {
      await PresentationGenerationApi.disableShare(presentationId);
      setShared(false);
      setToken(null);
      notify.success("공유를 껐습니다", "링크가 더 이상 열리지 않습니다.");
    } catch {
      notify.error("공유를 끄지 못했습니다", "다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  };

  const handleCopy = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      notify.error("복사하지 못했습니다", "링크를 직접 선택해 복사해 주세요.");
    }
  };

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className="flex items-center gap-1.5 rounded-full border border-[#EDECEC] bg-white px-3.5 py-2 text-sm font-medium text-[#101323] duration-300 hover:border-[#5141e5] hover:text-[#5141e5] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Share2 className="h-3.5 w-3.5" />
          공유
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[300px] rounded-[18px] p-0">
        <div className="border-b border-gray-100 px-4 py-3">
          <p className="text-sm font-semibold text-[#101323]">공유 링크</p>
          <p className="mt-0.5 text-xs text-gray-500">
            링크가 있는 누구나 읽기 전용으로 볼 수 있습니다.
          </p>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8 text-gray-400">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : shared ? (
          <div className="space-y-3 p-4">
            <div className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-2">
              <Link2 className="h-3.5 w-3.5 shrink-0 text-gray-400" />
              <input
                readOnly
                value={shareUrl}
                onFocus={(e) => e.currentTarget.select()}
                className="min-w-0 flex-1 bg-transparent text-xs text-[#101323] outline-none"
              />
              <button
                type="button"
                onClick={handleCopy}
                title="링크 복사"
                className="shrink-0 text-gray-500 duration-300 hover:text-[#5141e5]"
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5 text-green-600" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
            <div className="flex items-center justify-between">
              <button
                type="button"
                onClick={() => handleEnable(true)}
                disabled={busy}
                className="flex items-center gap-1 text-xs font-medium text-gray-600 duration-300 hover:text-[#5141e5] disabled:opacity-50"
              >
                <RefreshCw className="h-3 w-3" />
                링크 재발급
              </button>
              <button
                type="button"
                onClick={handleDisable}
                disabled={busy}
                className="text-xs font-medium text-red-500 duration-300 hover:text-red-600 disabled:opacity-50"
              >
                공유 끄기
              </button>
            </div>
          </div>
        ) : (
          <div className="p-4">
            <button
              type="button"
              onClick={() => handleEnable(false)}
              disabled={busy}
              className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-[#5141e5] px-3 py-2 text-sm font-medium text-white duration-300 hover:bg-[#4133c4] disabled:opacity-50"
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Link2 className="h-3.5 w-3.5" />
              )}
              공유 링크 만들기
            </button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
};

export default SharePopover;
