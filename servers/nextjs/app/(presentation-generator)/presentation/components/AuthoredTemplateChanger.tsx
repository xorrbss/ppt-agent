"use client";

import React, { useEffect, useMemo, useState } from "react";
import { LayoutTemplate, Loader2, Search, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useDispatch } from "react-redux";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { notify } from "@/components/ui/sonner";
import { clearPresentationData } from "@/store/slices/presentationGeneration";
import { clearHistory } from "@/store/slices/undoRedoSlice";
import AuthoredStylesApi, {
  DEFAULT_AUTHORED_STYLE,
} from "../../services/api/authored";
import type {
  AuthoredStyleCategory,
  AuthoredStyleSummary,
} from "../../services/api/authored";
import AuthoredStyleCard from "../../outline/components/AuthoredStyleCard";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";

const POLL_INTERVAL_MS = 2_000;
const GENERATION_TIMEOUT_MS = 30 * 60 * 1_000;
const MAX_POLL_ERRORS = 4;

const CATEGORY_LABELS: Record<AuthoredStyleCategory, string> = {
  general: "범용",
  business: "비즈니스",
  technology: "기술",
  research: "리서치",
  editorial: "에디토리얼",
  creative: "크리에이티브",
};

function normalizeSearchText(value: string) {
  return value.normalize("NFKC").trim().toLocaleLowerCase("ko-KR");
}

export default function AuthoredTemplateChanger({
  presentationId,
  currentStyleId,
  disabled = false,
}: {
  presentationId: string;
  currentStyleId?: string | null;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [styles, setStyles] = useState<AuthoredStyleSummary[]>([]);
  const [selectedStyleId, setSelectedStyleId] = useState(
    currentStyleId || DEFAULT_AUTHORED_STYLE.id
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [isLoadingStyles, setIsLoadingStyles] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [visionQa, setVisionQa] = useState(false);
  const router = useRouter();
  const dispatch = useDispatch();

  useEffect(() => {
    if (!open || styles.length > 0) return;

    let cancelled = false;
    setIsLoadingStyles(true);
    AuthoredStylesApi.getStyles()
      .then((nextStyles) => {
        if (cancelled) return;
        setStyles(nextStyles);
        const preferred = currentStyleId || DEFAULT_AUTHORED_STYLE.id;
        setSelectedStyleId(
          nextStyles.some((style) => style.id === preferred)
            ? preferred
            : nextStyles[0]?.id || DEFAULT_AUTHORED_STYLE.id
        );
      })
      .catch((error) => {
        if (cancelled) return;
        console.error("Failed to load AI-authored templates", error);
        notify.error(
          "AI 저작 템플릿을 불러오지 못했습니다",
          "잠시 후 다시 시도해 주세요."
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoadingStyles(false);
      });

    return () => {
      cancelled = true;
    };
  }, [currentStyleId, open, styles.length]);

  const filteredStyles = useMemo(() => {
    const query = normalizeSearchText(searchQuery);
    if (!query) return styles;
    return styles.filter((style) =>
      normalizeSearchText(
        [
          style.name,
          style.description,
          CATEGORY_LABELS[style.category],
          ...style.tags,
          ...style.use_cases,
        ].join(" ")
      ).includes(query)
    );
  }, [searchQuery, styles]);

  const changeTemplate = async () => {
    if (!selectedStyleId || isGenerating) return;

    setIsGenerating(true);
    try {
      const started =
        await PresentationGenerationApi.retemplateAuthoredPresentation(
          presentationId,
          {
            authored_style: selectedStyleId,
            vision_qa: visionQa,
          }
        );
      const taskId = started?.id;
      if (!taskId) throw new Error("생성 작업을 시작하지 못했습니다.");

      notify.success(
        "AI 저작 템플릿 변경을 시작했습니다",
        "저장된 슬라이드 원고로 새 자료를 생성합니다. 원본은 유지됩니다."
      );

      const deadline = Date.now() + GENERATION_TIMEOUT_MS;
      let pollErrors = 0;
      while (Date.now() < deadline) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, POLL_INTERVAL_MS)
        );

        try {
          const task =
            await PresentationGenerationApi.getGenerationStatus(taskId);
          pollErrors = 0;
          if (task?.status === "completed") {
            const newPresentationId = task?.data?.presentation_id;
            if (!newPresentationId) {
              throw new Error("생성된 프레젠테이션을 찾지 못했습니다.");
            }

            dispatch(clearPresentationData());
            dispatch(clearHistory());
            setOpen(false);
            router.push(`/presentation?id=${newPresentationId}`);
            return;
          }
          if (task?.status === "error") {
            throw new Error(
              task?.message || "AI 저작 프레젠테이션 생성에 실패했습니다."
            );
          }
        } catch (error) {
          pollErrors += 1;
          if (pollErrors >= MAX_POLL_ERRORS) throw error;
        }
      }

      throw new Error("생성 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.");
    } catch (error) {
      console.error("Error changing AI-authored template", error);
      notify.error(
        "AI 저작 템플릿 변경 실패",
        error instanceof Error
          ? error.message
          : "템플릿 변경 중 오류가 발생했습니다."
      );
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!isGenerating) setOpen(nextOpen);
      }}
    >
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          data-testid="template-change-trigger"
          className="h-10 rounded-[80px] px-5"
        >
          <LayoutTemplate className="h-4 w-4" />
          템플릿 변경
        </Button>
      </DialogTrigger>

      <DialogContent className="flex max-h-[90vh] max-w-6xl flex-col overflow-hidden p-0">
        <DialogHeader className="px-7 pb-2 pt-7">
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-[#5141E5]" />
            AI 저작 템플릿 변경
          </DialogTitle>
          <DialogDescription className="leading-6">
            기존 슬라이드의 LLM 원고와 순서는 그대로 유지하고, 선택한 AI 저작
            템플릿으로 새 자료를 만듭니다. 편집 가능한 PPT 변환은 내보내기
            단계에서만 적용됩니다.
          </DialogDescription>
        </DialogHeader>

        <div className="px-7 pb-3">
          <label className="relative block">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="이름, 용도, 태그로 AI 저작 템플릿 검색"
              className="h-10 w-full rounded-xl border border-[#E3E4E8] bg-white pl-10 pr-4 text-sm outline-none focus:border-[#5141E5] focus:ring-2 focus:ring-[#5141E5]/15"
            />
          </label>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-7 py-3">
          {isLoadingStyles ? (
            <div className="flex min-h-56 items-center justify-center gap-2 text-sm text-gray-600">
              <Loader2 className="h-5 w-5 animate-spin" />
              AI 저작 템플릿을 불러오는 중입니다.
            </div>
          ) : (
            <div
              role="radiogroup"
              aria-label="AI 저작 템플릿"
              data-testid="authored-template-grid"
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
            >
              {filteredStyles.map((style) => (
                <AuthoredStyleCard
                  key={style.id}
                  style={style}
                  categoryLabel={CATEGORY_LABELS[style.category]}
                  isSelected={style.id === selectedStyleId}
                  isTabStop={style.id === selectedStyleId}
                  onSelect={setSelectedStyleId}
                />
              ))}
            </div>
          )}
          {!isLoadingStyles && filteredStyles.length === 0 && (
            <p className="py-16 text-center text-sm text-gray-500">
              검색 조건에 맞는 AI 저작 템플릿이 없습니다.
            </p>
          )}
        </div>

        <DialogFooter className="items-center border-t bg-white px-7 py-5 sm:justify-between">
          <label className="flex cursor-pointer items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={visionQa}
              disabled={isGenerating}
              onChange={(event) => setVisionQa(event.target.checked)}
              className="h-4 w-4 rounded border-gray-300 accent-[#5141E5]"
            />
            고품질 검수 추가
            <span className="text-xs text-gray-500">(더 느리지만 시각 오류 재검사)</span>
          </label>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={isGenerating}
              onClick={() => setOpen(false)}
            >
              취소
            </Button>
            <Button
              type="button"
              disabled={
                !selectedStyleId || isLoadingStyles || isGenerating
              }
              data-testid="template-change-confirm"
              onClick={changeTemplate}
            >
              {isGenerating && <Loader2 className="h-4 w-4 animate-spin" />}
              {isGenerating
                ? "AI 저작 자료 생성 중"
                : "이 템플릿으로 새로 만들기"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
