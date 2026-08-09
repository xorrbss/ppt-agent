"use client";
import React, { useState } from "react";
import { History, Loader2, RotateCcw } from "lucide-react";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { notify } from "@/components/ui/sonner";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import type { WorkspaceCommandDispatcher } from "../workspace";

interface VersionSummary {
  id: string;
  created_at: string;
  label?: string | null;
  slide_count: number;
}

function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("ko-KR", {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const VersionHistoryPopover = ({
  presentationId,
  disabled,
  allowRestore = true,
  workspaceCommandsEnabled = false,
  onWorkspaceCommand,
}: {
  presentationId: string;
  disabled?: boolean;
  allowRestore?: boolean;
  workspaceCommandsEnabled?: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
}) => {
  const [open, setOpen] = useState(false);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  // Restores run as durable workspace commands; the legacy REST route is fenced.
  const restoreAllowed =
    allowRestore && workspaceCommandsEnabled && Boolean(onWorkspaceCommand);

  const loadVersions = async () => {
    setLoading(true);
    try {
      const data = await PresentationGenerationApi.getPresentationVersions(
        presentationId
      );
      setVersions(Array.isArray(data) ? data : []);
    } catch {
      notify.error("버전 기록을 불러오지 못했습니다", "다시 시도해 주세요.");
    } finally {
      setLoading(false);
    }
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (next) loadVersions();
  };

  const handleRestore = async (version: VersionSummary) => {
    if (!restoreAllowed) return;
    setRestoringId(version.id);
    try {
      await onWorkspaceCommand?.(
        `${formatWhen(version.created_at)} 버전으로 복원해 줘`,
        {
          commandType: "version.restore",
          context: { version_id: version.id, source: "version_history" },
        }
      );
      // The workspace card owns approval, progress, result, and errors.
      setOpen(false);
    } catch {
      // The durable timeline reports the failure next to its command card.
    } finally {
      setRestoringId(null);
    }
  };

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="version-history-trigger"
          title="버전 기록"
          disabled={disabled}
          className="group cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
        >
          <History className="h-3.5 w-3.5 text-[#101323] duration-300 group-hover:text-[#5141e5]" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[280px] rounded-[18px] p-0">
        <div className="border-b border-gray-100 px-4 py-3">
          <p className="text-sm font-semibold text-[#101323]">버전 기록</p>
          <p className="mt-0.5 text-xs text-gray-500">
            저장 시점으로 슬라이드를 되돌립니다.
          </p>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8 text-gray-400">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : versions.length === 0 ? (
          <p className="px-4 py-8 text-center text-xs text-gray-400">
            아직 저장된 버전이 없습니다.
          </p>
        ) : (
          <ul className="max-h-[320px] overflow-y-auto py-1">
            {versions.map((version) => (
              <li
                key={version.id}
                className="flex items-center justify-between gap-2 px-4 py-2.5 hover:bg-gray-50"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-[#101323]">
                    {formatWhen(version.created_at)}
                  </p>
                  <p className="truncate text-xs text-gray-500">
                    {version.label ? `${version.label} · ` : ""}
                    {version.slide_count}개 슬라이드
                  </p>
                </div>
                <button
                  type="button"
                  data-testid="version-restore"
                  onClick={() => handleRestore(version)}
                  disabled={!restoreAllowed || restoringId !== null}
                  className={`flex shrink-0 items-center gap-1 rounded-full border border-gray-200 px-2.5 py-1 text-xs font-medium text-[#101323] duration-300 hover:border-[#5141e5] hover:text-[#5141e5] disabled:cursor-not-allowed disabled:opacity-50 ${
                    restoreAllowed ? "" : "hidden"
                  }`}
                >
                  {restoringId === version.id ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <RotateCcw className="h-3 w-3" />
                  )}
                  복원
                </button>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
};

export default VersionHistoryPopover;
