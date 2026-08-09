"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Check, Copy, Eye, Link2, Loader2, RotateCw } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";

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
  const t = useTranslations("share");
  const format = useFormatter();
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
          notify.error(t("toasts.loadError"));
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadAttempt, open, presentationId, t]);

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
        regenerate ? t("toasts.regenerated") : t("toasts.saved"),
      );
    } catch {
      notify.error(t("toasts.saveError"));
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
      notify.success(t("toasts.disabled"));
    } catch {
      notify.error(t("toasts.disableError"));
    } finally {
      setAction(null);
    }
  };

  const copy = async () => {
    if (!shareUrl) return;
    await navigator.clipboard.writeText(shareUrl);
    setCopied(true);
    notify.success(t("toasts.copied"));
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
            {t("title")}
          </DialogTitle>
          <DialogDescription className="truncate pr-8">
            {title || t("untitled")}
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
                {t("loadError")}
              </p>
              <button
                type="button"
                onClick={() => setLoadAttempt((current) => current + 1)}
                className="h-9 rounded-lg border border-[#D9D9DF] px-3 text-sm font-medium text-[#344054] hover:bg-[#F8F9FB]"
              >
                {t("retry")}
              </button>
            </div>
          ) : (
            <>
              {status?.expired && (
                <div
                  role="status"
                  className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
                >
                  {t("expired")}
                </div>
              )}

              {hasActiveLink && shareUrl && (
                <div className="space-y-2">
                  <label className="text-xs font-medium text-[#667085]">
                    {t("linkLabel")}
                  </label>
                  <div className="flex gap-2">
                    <input
                      readOnly
                      value={shareUrl}
                      aria-label={t("linkLabel")}
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
                      {copied ? t("copied") : t("copy")}
                    </button>
                  </div>
                </div>
              )}

              <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
                <label className="space-y-2">
                  <span className="block text-xs font-medium text-[#667085]">
                    {t("expirationLabel")}
                  </span>
                  <select
                    value={expiration}
                    onChange={(event) =>
                      setExpiration(event.target.value as ExpirationChoice)
                    }
                    className="h-10 w-full rounded-lg border border-[#D9D9DF] bg-white px-3 text-sm text-[#344054] outline-none focus:border-[#7A5AF8] focus:ring-2 focus:ring-[#7A5AF8]/20"
                  >
                    <option value="never">{t("expiration.never")}</option>
                    <option value="1">{t("expiration.oneDay")}</option>
                    <option value="7">{t("expiration.sevenDays")}</option>
                    <option value="30">{t("expiration.thirtyDays")}</option>
                  </select>
                </label>
                <div className="flex h-10 items-center gap-2 rounded-lg bg-[#F2F4F7] px-3 text-sm text-[#475467]">
                  <Eye className="h-4 w-4" />
                  {t("views", { count: status?.view_count ?? 0 })}
                </div>
              </div>

              {status?.expires_at && hasActiveLink && (
                <p className="text-xs text-[#667085]">
                  {t("expiresAt", {
                    date: format.dateTime(new Date(status.expires_at), {
                      dateStyle: "medium",
                      timeStyle: "short",
                    }),
                  })}
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
                      {action === "disable" ? t("disabling") : t("disable")}
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
                      {action === "rotate" ? t("regenerating") : t("regenerate")}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void save(false)}
                    className="h-10 rounded-lg bg-[#5146E5] px-4 text-sm font-medium text-white hover:bg-[#4338CA] disabled:opacity-50"
                  >
                    {action === "save"
                      ? t("saving")
                      : hasActiveLink
                        ? t("save")
                        : status?.expired
                          ? t("createNew")
                          : t("create")}
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
