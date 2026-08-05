"use client";

import { useState } from "react";
import { CheckCircle2, Loader2, RefreshCw, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { notify } from "@/components/ui/sonner";
import { resolveBackendAssetUrl } from "@/utils/api";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import type { WorkspaceCommandDispatcher } from "../workspace";

const POLL_INTERVAL_MS = 2_000;
const PREVIEW_TIMEOUT_MS = 15 * 60 * 1_000;

type PageRemakePreview = {
  kind: "authored_page_remake_preview";
  phase: "preview_ready" | "published";
  slide_index: number;
  candidate_image: string;
  candidate_digest: string;
  source_digest: string;
  baseline_issues?: Array<{ category?: string; detail?: string }>;
  candidate_issues?: Array<{ category?: string; detail?: string }>;
  validation?: Record<string, string>;
  generation?: { elapsed_ms?: number; llm_scope?: string };
};

function errorMessage(error: unknown, fallback: string) {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function PageRemakeFlow({
  presentationId,
  currentSlide,
  disabled = false,
  onWorkspaceCommand,
  onReload,
  onDismiss,
}: {
  presentationId: string;
  currentSlide: number;
  disabled?: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  onReload?: () => void;
  onDismiss: () => void;
}) {
  const [previewTaskId, setPreviewTaskId] = useState<string | null>(null);
  const [preview, setPreview] = useState<PageRemakePreview | null>(null);
  const [statusMessage, setStatusMessage] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const pageNumber = currentSlide + 1;

  const createPreview = async () => {
    if (isGenerating || disabled) return;
    setIsGenerating(true);
    setPreview(null);
    setPreviewTaskId(null);
    setStatusMessage(`${pageNumber}페이지의 새 버전을 만들고 있습니다.`);
    try {
      const started = await PresentationGenerationApi.previewAuthoredPageRemake(
        presentationId,
        currentSlide,
      );
      const taskId = started?.id;
      if (!taskId) throw new Error("페이지 다시 만들기 작업을 시작하지 못했습니다.");
      setPreviewTaskId(taskId);

      const deadline = Date.now() + PREVIEW_TIMEOUT_MS;
      while (Date.now() < deadline) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, POLL_INTERVAL_MS),
        );
        const task = await PresentationGenerationApi.getGenerationStatus(taskId);
        if (task?.message) setStatusMessage(task.message);
        if (task?.status === "completed") {
          const data = task.data as PageRemakePreview | undefined;
          if (
            !data ||
            data.kind !== "authored_page_remake_preview" ||
            data.slide_index !== currentSlide ||
            data.phase !== "preview_ready"
          ) {
            throw new Error(
              "페이지 미리보기 결과가 현재 페이지와 일치하지 않습니다.",
            );
          }
          setPreview(data);
          notify.success("새 페이지 미리보기가 준비되었습니다.");
          return;
        }
        if (task?.status === "error") {
          const detail = task?.error?.detail;
          throw new Error(
            typeof detail === "string"
              ? detail
              : "페이지를 다시 만들지 못했습니다.",
          );
        }
      }
      throw new Error(
        "페이지 생성 시간이 초과되었습니다. 다시 시도해 주세요.",
      );
    } catch (error) {
      notify.error(
        "페이지 다시 만들기 실패",
        errorMessage(error, "페이지를 다시 만드는 중 오류가 발생했습니다."),
      );
      setStatusMessage("");
    } finally {
      setIsGenerating(false);
    }
  };

  const publishPreview = async () => {
    if (!preview || !previewTaskId || isPublishing || disabled) return;
    setIsPublishing(true);
    setStatusMessage(`${pageNumber}페이지의 새 버전을 적용하고 있습니다.`);
    try {
      if (onWorkspaceCommand) {
        await onWorkspaceCommand(`${pageNumber}페이지 미리보기를 적용해 줘`, {
          commandType: "slide.regenerate",
          context: {
            slide_index: currentSlide,
            preview_task_id: previewTaskId,
            source: "page_remake_button",
          },
        });
        notify.success("페이지 적용 작업을 대화에 추가했습니다.");
      } else {
        await PresentationGenerationApi.publishAuthoredPageRemake(
          presentationId,
          currentSlide,
          previewTaskId,
        );
        onReload?.();
        notify.success(`${pageNumber}페이지의 새 버전을 적용했습니다.`);
      }
      onDismiss();
    } catch (error) {
      notify.error(
        "페이지 적용 실패",
        errorMessage(
          error,
          "원본이 변경되었거나 새 버전을 적용하지 못했습니다.",
        ),
      );
      setStatusMessage("");
    } finally {
      setIsPublishing(false);
    }
  };

  return (
    <>
      {!preview ? (
        <section className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-5">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
            <div className="space-y-1 text-sm text-slate-700">
              <p className="font-semibold text-slate-950">현재 페이지 전용 작업</p>
              <p>내용과 의미는 유지하고 배치와 시각 구조를 새로 구성합니다.</p>
              <p>
                생성 결과는 별도 미리보기로 검증한 뒤 사용자가 승인해야
                적용됩니다.
              </p>
            </div>
          </div>
          {statusMessage && (
            <p data-testid="page-remake-status" className="text-sm text-slate-600">
              {statusMessage}
            </p>
          )}
        </section>
      ) : (
        <div className="space-y-4" data-testid="page-remake-preview">
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            {/* eslint-disable-next-line @next/next/no-img-element -- immutable runtime preview */}
            <img
              src={resolveBackendAssetUrl(preview.candidate_image)}
              alt={`${pageNumber}페이지 새 버전 미리보기`}
              className="aspect-video w-full object-cover"
            />
          </div>
          <div className="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950">
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0" />
            <div>
              <p className="font-semibold">미리보기 검증 완료</p>
              <p>
                렌더링과 DOM 안전 검사를 통과했습니다. 원본은 아직 변경되지
                않았습니다.
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          disabled={isGenerating || isPublishing}
          onClick={onDismiss}
        >
          취소
        </Button>
        {!preview ? (
          <Button
            type="button"
            disabled={isGenerating || disabled}
            onClick={createPreview}
            data-testid="page-remake-preview-create"
          >
            {isGenerating && <Loader2 className="h-4 w-4 animate-spin" />}
            {isGenerating ? "미리보기 만드는 중" : "미리보기 만들기"}
          </Button>
        ) : (
          <Button
            type="button"
            disabled={isPublishing || disabled}
            onClick={publishPreview}
            data-testid="page-remake-publish"
          >
            {isPublishing && <Loader2 className="h-4 w-4 animate-spin" />}
            {isPublishing ? "적용 중" : "이 버전 적용"}
          </Button>
        )}
      </div>
    </>
  );
}

export function WorkspacePageRemakePrompt({
  presentationId,
  currentSlide,
  disabled = false,
  onWorkspaceCommand,
  onDismiss,
}: {
  presentationId: string;
  currentSlide: number;
  disabled?: boolean;
  onWorkspaceCommand: WorkspaceCommandDispatcher;
  onDismiss: () => void;
}) {
  return (
    <article
      className="rounded-2xl border border-indigo-200 bg-white p-4 shadow-sm"
      data-testid="page-remake-workspace-prompt"
      aria-label="페이지 다시 만들기"
      aria-live="polite"
    >
      <div className="mb-4 flex items-start gap-3">
        <span
          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-50 text-sm text-indigo-600"
          aria-hidden="true"
        >
          ✦
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-900">
            페이지 다시 만들기
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            {currentSlide + 1}페이지만 새 레이아웃으로 만듭니다. 미리보기 승인
            전에는 원본을 변경하지 않습니다.
          </p>
        </div>
      </div>
      <PageRemakeFlow
        presentationId={presentationId}
        currentSlide={currentSlide}
        disabled={disabled}
        onWorkspaceCommand={onWorkspaceCommand}
        onDismiss={onDismiss}
      />
    </article>
  );
}

export default function AuthoredPageRemake({
  presentationId,
  currentSlide,
  disabled = false,
  chatFirstEnabled = false,
  workspaceCommandsEnabled = false,
  onWorkspacePageRemakeRequest,
  onReload,
}: {
  presentationId: string;
  currentSlide?: number;
  disabled?: boolean;
  chatFirstEnabled?: boolean;
  workspaceCommandsEnabled?: boolean;
  onWorkspacePageRemakeRequest?: (slideIndex: number) => void;
  onReload?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const validSlide =
    typeof currentSlide === "number" &&
    Number.isInteger(currentSlide) &&
    currentSlide >= 0;

  if (chatFirstEnabled) {
    return (
      <Button
        type="button"
        variant="outline"
        disabled={
          disabled ||
          !validSlide ||
          !workspaceCommandsEnabled ||
          !onWorkspacePageRemakeRequest
        }
        data-testid="page-remake-trigger"
        className="h-10 rounded-[80px] px-5"
        onClick={() => {
          if (validSlide) onWorkspacePageRemakeRequest?.(currentSlide);
        }}
      >
        <RefreshCw className="h-4 w-4" />
        페이지 다시 만들기
      </Button>
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={disabled || !validSlide}
          data-testid="page-remake-trigger"
          className="h-10 rounded-[80px] px-5"
        >
          <RefreshCw className="h-4 w-4" />
          페이지 다시 만들기
        </Button>
      </DialogTrigger>
      {validSlide && (
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>페이지 다시 만들기</DialogTitle>
            <DialogDescription>
              {currentSlide + 1}페이지만 새 레이아웃으로 다시 만듭니다. 전체
              검수에서는 이 작업이 자동으로 실행되지 않으며, 적용하기 전까지
              원본은 변경되지 않습니다.
            </DialogDescription>
          </DialogHeader>
          <PageRemakeFlow
            presentationId={presentationId}
            currentSlide={currentSlide}
            onReload={onReload}
            onDismiss={() => setOpen(false)}
          />
        </DialogContent>
      )}
    </Dialog>
  );
}
