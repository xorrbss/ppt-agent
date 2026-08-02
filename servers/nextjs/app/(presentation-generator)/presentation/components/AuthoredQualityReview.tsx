"use client";

import React, { useEffect, useState } from "react";
import {
  AlertTriangle,
  BadgeCheck,
  History,
  Loader2,
  ScanSearch,
  Sparkles,
} from "lucide-react";

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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { notify } from "@/components/ui/sonner";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import {
  normalizeAuthoredQualityReviewResult,
  type AuthoredQualityReviewOcrCheck,
  type AuthoredQualityReviewOcrOmissions,
  type AuthoredQualityReviewResult,
  type AuthoredReviewRepairPath,
} from "../../services/api/authored";

const POLL_INTERVAL_MS = 2_000;
const REVIEW_TIMEOUT_MS = 30 * 60 * 1_000;
const MAX_POLL_ERRORS = 4;
const REVIEW_TASK_STORAGE_PREFIX = "presenton:authored-quality-review:task:";

type ReviewScope = "all" | "current";
type ReviewMode = "analyze_only" | "analyze_and_fix";

function slideNumbers(indices?: number[]) {
  if (!indices?.length) return "";
  return indices.map((index) => index + 1).join(", ");
}

function reviewPathLabel(path: AuthoredReviewRepairPath) {
  if (path === "local_patch") return "국소 패치";
  if (path === "full_fallback") return "전체 재작성 fallback";
  return "변경 없음";
}

function reviewOutcomeLabel(attempted: boolean, applied: boolean) {
  if (applied) return "실제 적용됨";
  if (attempted) return "시도했으나 미채택";
  return "변경 없음";
}

function ocrStatusLabel(ocr: AuthoredQualityReviewOcrCheck) {
  if (ocr.executed || ocr.status === "executed") return "OCR 실행됨";
  if (ocr.status === "routed_skip") return "OCR 미실행 · 로컬 라우팅으로 생략";
  if (ocr.status === "unavailable") return "OCR 실행 불가";
  if (ocr.status === "timeout") return "OCR 시간 초과";
  return "OCR 실행 여부 확인 불가";
}

function ocrOmissionLabel(omissions: AuthoredQualityReviewOcrOmissions) {
  return `텍스트 ${omissions.text} · 숫자 ${omissions.numbers} · 기호 ${omissions.symbols}`;
}

function formatDuration(milliseconds?: number) {
  if (milliseconds === undefined) return null;
  return milliseconds >= 1_000
    ? `${(milliseconds / 1_000).toFixed(1)}초`
    : `${Math.round(milliseconds)}ms`;
}

function reviewTaskStorageKey(presentationId: string) {
  return `${REVIEW_TASK_STORAGE_PREFIX}${presentationId}`;
}

export default function AuthoredQualityReview({
  presentationId,
  currentSlide = 0,
  slideCount,
  disabled = false,
  onReload,
}: {
  presentationId: string;
  currentSlide?: number;
  slideCount: number;
  disabled?: boolean;
  onReload?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<ReviewScope>("all");
  const [mode, setMode] = useState<ReviewMode>("analyze_and_fix");
  const [isReviewing, setIsReviewing] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [result, setResult] = useState<AuthoredQualityReviewResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    const storageKey = reviewTaskStorageKey(presentationId);
    const taskId = window.sessionStorage.getItem(storageKey);

    setResult(null);
    setStatusMessage("");
    if (!taskId) return;

    const restoreCompletedReview = async () => {
      try {
        const task = await PresentationGenerationApi.getGenerationStatus(taskId);
        if (cancelled) return;

        if (task?.status === "completed") {
          setResult(normalizeAuthoredQualityReviewResult(task.data));
          setStatusMessage(task.message || "");
        } else if (task?.status === "error") {
          window.sessionStorage.removeItem(storageKey);
        }
      } catch {
        // Keep the task ID for a later refresh. A transient API failure must not
        // discard an otherwise durable backend review result.
      }
    };

    void restoreCompletedReview();
    return () => {
      cancelled = true;
    };
  }, [presentationId]);

  const startReview = async () => {
    if (isReviewing) return;
    setIsReviewing(true);
    setResult(null);
    setStatusMessage("고품질 검수 작업을 시작하고 있습니다.");

    try {
      const started =
        await PresentationGenerationApi.reviewAuthoredPresentation(
          presentationId,
          {
            scope,
            slide_indices: scope === "current" ? [currentSlide] : [],
            mode,
          }
        );
      const taskId = started?.id;
      if (!taskId) throw new Error("검수 작업을 시작하지 못했습니다.");
      window.sessionStorage.setItem(reviewTaskStorageKey(presentationId), taskId);

      const deadline = Date.now() + REVIEW_TIMEOUT_MS;
      let pollErrors = 0;
      while (Date.now() < deadline) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, POLL_INTERVAL_MS)
        );

        try {
          const task =
            await PresentationGenerationApi.getGenerationStatus(taskId);
          pollErrors = 0;
          if (task?.message) setStatusMessage(task.message);

          if (task?.status === "completed") {
            const nextResult = normalizeAuthoredQualityReviewResult(task.data);
            setResult(nextResult);
            if (mode === "analyze_and_fix" && nextResult.fixed_count > 0) {
              onReload?.();
            }
            notify.success(
              "고품질 검수가 완료되었습니다",
              nextResult.fixed_count > 0
                ? `${nextResult.fixed_count}개 슬라이드를 수정했습니다.`
                : nextResult.issue_slide_count > 0
                  ? `${nextResult.issue_slide_count}개 슬라이드에서 문제를 확인했습니다.`
                  : "수정이 필요한 시각적 문제가 없습니다."
            );
            return;
          }
          if (task?.status === "error") {
            throw new Error(task?.message || "고품질 검수에 실패했습니다.");
          }
        } catch (error) {
          pollErrors += 1;
          if (pollErrors >= MAX_POLL_ERRORS) throw error;
        }
      }
      throw new Error("검수 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.");
    } catch (error) {
      console.error("Error reviewing AI-authored presentation", error);
      notify.error(
        "고품질 검수 실패",
        error instanceof Error
          ? error.message
          : "검수 중 오류가 발생했습니다."
      );
      setStatusMessage("");
    } finally {
      setIsReviewing(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!isReviewing) setOpen(nextOpen);
      }}
    >
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          data-testid="quality-review-trigger"
          className="h-10 rounded-[80px] px-5"
        >
          <ScanSearch className="h-4 w-4" />
          고품질 검수
        </Button>
      </DialogTrigger>

      <DialogContent className="max-w-xl p-0">
        <DialogHeader className="px-7 pb-2 pt-7">
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-[#5141E5]" />
            기존 자료 고품질 검수
          </DialogTitle>
          <DialogDescription className="leading-6">
            저장된 AI 저작 슬라이드를 다시 렌더링해 글자 잘림, 요소 겹침,
            정렬, 대비 문제를 검사합니다.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 px-7 py-4">
          {isReviewing ? (
            <div
              className="flex min-h-48 flex-col items-center justify-center rounded-2xl border border-[#E3E4E8] bg-[#F8F8FB] px-6 text-center"
              role="status"
              aria-live="polite"
            >
              <Loader2 className="mb-4 h-8 w-8 animate-spin text-[#5141E5]" />
              <p className="font-semibold text-[#191919]">검수 진행 중</p>
              <p className="mt-2 text-sm leading-6 text-gray-600">
                {statusMessage}
              </p>
            </div>
          ) : result ? (
            <div className="space-y-4">
              <div className="flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
                <BadgeCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
                <div>
                  <p className="font-semibold text-emerald-950">검수 완료</p>
                  <p className="mt-1 text-sm leading-6 text-emerald-900">
                    {result.reviewed_count}개 슬라이드를 검사했고{" "}
                    {result.issue_slide_count}개에서 문제를 확인했습니다.
                    {result.fixed_count > 0 &&
                      ` ${result.fixed_count}개를 자동 수정했습니다.`}
                  </p>
                </div>
              </div>

              {result.fixed_count > 0 && (
                <p className="text-sm text-gray-700">
                  수정된 슬라이드:{" "}
                  <span className="font-semibold">
                    {slideNumbers(result.fixed_slide_indices)}
                  </span>
                </p>
              )}
              {result.remaining_issue_count > 0 && (
                <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <p>
                    재검증 후 {result.remaining_issue_count}개 슬라이드에 확인이
                    필요합니다
                    {result.remaining_slide_indices?.length
                      ? `: ${slideNumbers(result.remaining_slide_indices)}`
                      : "."}
                  </p>
                </div>
              )}
              {result.version_saved && (
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <History className="h-4 w-4" />
                  수정 전 상태를 버전 기록에 저장했습니다.
                </div>
              )}
              {(result.metrics.total_ms !== undefined ||
                Object.keys(result.metrics.stage_timings_ms).length > 0 ||
                result.metrics.cache_hits !== undefined ||
                 result.metrics.vqa_calls !== undefined ||
                 result.metrics.patch_count !== undefined ||
                 result.metrics.fallback_count !== undefined ||
                 result.metrics.patch_attempted !== undefined ||
                 result.metrics.patch_succeeded !== undefined ||
                 result.metrics.patch_failed !== undefined ||
                 result.metrics.fallback_attempted !== undefined ||
                 result.metrics.fallback_succeeded !== undefined ||
                 result.metrics.fallback_failed !== undefined ||
                 result.metrics.ocr_executed !== undefined ||
                 result.metrics.ocr_calls !== undefined ||
                 result.metrics.ocr_cache_hits !== undefined ||
                 result.metrics.ocr_total_ms !== undefined) && (
                <section
                  data-testid="quality-review-metrics"
                  className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs text-slate-700"
                >
                  <p className="font-semibold text-slate-900">검수 처리 내역</p>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                    {result.metrics.total_ms !== undefined && (
                      <span>총 {formatDuration(result.metrics.total_ms)}</span>
                    )}
                    {result.metrics.cache_hits !== undefined && (
                      <span>캐시 적중 {result.metrics.cache_hits}</span>
                    )}
                    {result.metrics.vqa_calls !== undefined && (
                      <span>VQA 호출 {result.metrics.vqa_calls}</span>
                    )}
                    {result.metrics.patch_count !== undefined && (
                      <span>국소 패치 {result.metrics.patch_count}</span>
                    )}
                    {result.metrics.fallback_count !== undefined && (
                      <span>fallback {result.metrics.fallback_count}</span>
                    )}
                    {result.metrics.patch_attempted !== undefined && (
                      <span>국소 패치 시도 {result.metrics.patch_attempted}</span>
                    )}
                    {result.metrics.patch_succeeded !== undefined && (
                      <span>국소 패치 성공 {result.metrics.patch_succeeded}</span>
                    )}
                    {result.metrics.patch_failed !== undefined && (
                      <span>국소 패치 실패 {result.metrics.patch_failed}</span>
                    )}
                    {result.metrics.fallback_attempted !== undefined && (
                      <span>전체 재작성 시도 {result.metrics.fallback_attempted}</span>
                    )}
                    {result.metrics.fallback_succeeded !== undefined && (
                      <span>전체 재작성 성공 {result.metrics.fallback_succeeded}</span>
                    )}
                    {result.metrics.fallback_failed !== undefined && (
                      <span>전체 재작성 실패 {result.metrics.fallback_failed}</span>
                    )}
                    {result.metrics.ocr_executed !== undefined && (
                      <span>
                        {result.metrics.ocr_executed ? "OCR 실행됨" : "OCR 미실행"}
                      </span>
                    )}
                    {result.metrics.ocr_calls !== undefined && (
                      <span>OCR 호출 {result.metrics.ocr_calls}</span>
                    )}
                    {result.metrics.ocr_cache_hits !== undefined && (
                      <span>OCR 캐시 적중 {result.metrics.ocr_cache_hits}</span>
                    )}
                    {result.metrics.ocr_total_ms !== undefined && (
                      <span>OCR 총 {formatDuration(result.metrics.ocr_total_ms)}</span>
                    )}
                  </div>
                  {Object.keys(result.metrics.stage_timings_ms).length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-slate-600">
                      {Object.entries(result.metrics.stage_timings_ms).map(
                        ([stage, duration]) => (
                          <span key={stage}>
                            {stage}: {formatDuration(duration)}
                          </span>
                        )
                      )}
                    </div>
                  )}
                </section>
              )}
              {result.slide_results.length > 0 && (
                <section data-testid="quality-review-details" className="space-y-2">
                  <p className="text-sm font-semibold text-[#191919]">슬라이드별 변경</p>
                  {result.slide_results.map((slide) => (
                    <details
                      key={slide.slide_index}
                      open
                      data-testid={`quality-review-slide-${slide.slide_index}`}
                      className="rounded-xl border border-[#E3E4E8] bg-white px-4 py-3 text-sm"
                    >
                      <summary className="cursor-pointer font-medium text-[#191919]">
                        <span className="mr-1 font-semibold">
                          {reviewOutcomeLabel(slide.attempted, slide.applied)} ·
                        </span>
                        {slide.slide_index + 1}번 슬라이드 · {reviewPathLabel(slide.path)}
                      </summary>
                      <div className="mt-3 space-y-2 text-xs text-gray-700">
                        {slide.attempted && !slide.applied && slide.rejection_reason && (
                          <p className="text-amber-700">
                            미채택 사유: {slide.rejection_reason}
                          </p>
                        )}
                        {slide.ocr && (
                          <div className="rounded-lg border border-sky-100 bg-sky-50 p-3 text-sky-950">
                            <p className="font-medium">{ocrStatusLabel(slide.ocr)}</p>
                            {slide.ocr.executed && (
                              <>
                                <p className="mt-1">OCR 비교: baseline → candidate</p>
                                {slide.ocr.baseline && (
                                  <p className="mt-1">기준 누락: {ocrOmissionLabel(slide.ocr.baseline)}</p>
                                )}
                                {slide.ocr.candidate && (
                                  <p className="mt-1">후보 누락: {ocrOmissionLabel(slide.ocr.candidate)}</p>
                                )}
                                {(slide.ocr.comparison?.accepted === false || slide.ocr.comparison?.no_regression === false) && (
                                  <p className="mt-1 text-amber-700">후보 판정: OCR 회귀로 미채택</p>
                                )}
                                {slide.ocr.comparison?.reason && (
                                  <p className="mt-1 text-amber-700">OCR 판정 사유: {slide.ocr.comparison.reason}</p>
                                )}
                                {slide.ocr.comparison?.resolved && (
                                  <p className="mt-1 text-emerald-700">해결된 OCR 누락: {ocrOmissionLabel(slide.ocr.comparison.resolved)}</p>
                                )}
                                {slide.ocr.comparison?.remaining && (
                                  <p className="mt-1 text-amber-700">남은 OCR 누락: {ocrOmissionLabel(slide.ocr.comparison.remaining)}</p>
                                )}
                                {slide.ocr.comparison?.regressions && (
                                  <p className="mt-1 text-amber-700">후보 OCR 회귀: {ocrOmissionLabel(slide.ocr.comparison.regressions)}</p>
                                )}
                              </>
                            )}
                            {!slide.ocr.executed && slide.ocr.reason && (
                              <p className="mt-1 text-amber-700">사유: {slide.ocr.reason}</p>
                            )}
                          </div>
                        )}
                        {slide.element_changes.map((change, index) => (
                          <div
                            key={`${change.element_id ?? "slide"}-${index}`}
                            className="rounded-lg bg-slate-50 p-3"
                          >
                            <p className="font-medium text-slate-900">
                              <span className={change.applied ? "text-emerald-700" : "text-amber-700"}>
                                {change.applied ? "적용된 변경" : "미채택 후보"} ·
                              </span>
                              요소 {change.element_id ?? "슬라이드 수준"} · {reviewPathLabel(change.path)}
                            </p>
                            {!change.applied && (change.before !== undefined || change.after !== undefined) && (
                              <p className="mt-1 text-amber-700">
                                아래 비교값은 실제 슬라이드에 적용되지 않은 미채택 후보입니다.
                              </p>
                            )}
                            {(change.before !== undefined || change.after !== undefined) && (
                              <p className="mt-1 break-words">
                                전: {change.before ?? "—"} → 후: {change.after ?? "—"}
                              </p>
                            )}
                            {change.resolved_issues.length > 0 && (
                              <p className="mt-1 text-emerald-700">
                                해결됨: {change.resolved_issues.join(" · ")}
                              </p>
                            )}
                            {change.remaining_issues.length > 0 && (
                              <p className="mt-1 text-amber-700">
                                남은 이슈: {change.remaining_issues.join(" · ")}
                              </p>
                            )}
                          </div>
                        ))}
                        {slide.resolved_issues.length > 0 && (
                          <p className="text-emerald-700">
                            해결됨: {slide.resolved_issues.join(" · ")}
                          </p>
                        )}
                        {slide.remaining_issues.length > 0 && (
                          <p className="text-amber-700">
                            남은 이슈: {slide.remaining_issues.join(" · ")}
                          </p>
                        )}
                      </div>
                    </details>
                  ))}
                </section>
              )}
            </div>
          ) : (
            <>
              <fieldset>
                <legend className="mb-3 text-sm font-semibold text-[#191919]">
                  검수 범위
                </legend>
                <RadioGroup
                  value={scope}
                  onValueChange={(value) => setScope(value as ReviewScope)}
                  className="grid grid-cols-2 gap-3"
                >
                  <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-[#E3E4E8] p-4">
                    <RadioGroupItem value="all" />
                    <span>
                      <span className="block text-sm font-semibold">전체 슬라이드</span>
                      <span className="mt-1 block text-xs text-gray-500">
                        총 {slideCount}개
                      </span>
                    </span>
                  </label>
                  <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-[#E3E4E8] p-4">
                    <RadioGroupItem value="current" />
                    <span>
                      <span className="block text-sm font-semibold">현재 슬라이드</span>
                      <span className="mt-1 block text-xs text-gray-500">
                        {currentSlide + 1}번만 빠르게 검수
                      </span>
                    </span>
                  </label>
                </RadioGroup>
              </fieldset>

              <fieldset>
                <legend className="mb-3 text-sm font-semibold text-[#191919]">
                  처리 방식
                </legend>
                <RadioGroup
                  value={mode}
                  onValueChange={(value) => setMode(value as ReviewMode)}
                  className="space-y-3"
                >
                  <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-[#E3E4E8] p-4">
                    <RadioGroupItem value="analyze_and_fix" className="mt-0.5" />
                    <span>
                      <span className="block text-sm font-semibold">
                        문제 확인 후 자동 수정
                      </span>
                      <span className="mt-1 block text-xs leading-5 text-gray-500">
                        문제가 있는 슬라이드만 다시 만들고 결과를 재검증합니다.
                      </span>
                    </span>
                  </label>
                  <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-[#E3E4E8] p-4">
                    <RadioGroupItem value="analyze_only" className="mt-0.5" />
                    <span>
                      <span className="block text-sm font-semibold">
                        문제만 확인
                      </span>
                      <span className="mt-1 block text-xs leading-5 text-gray-500">
                        자료를 변경하지 않고 검수 결과만 확인합니다.
                      </span>
                    </span>
                  </label>
                </RadioGroup>
              </fieldset>

              {mode === "analyze_and_fix" && (
                <div className="flex items-center gap-2 rounded-xl bg-[#F6F3FF] px-4 py-3 text-xs text-[#5141E5]">
                  <History className="h-4 w-4 shrink-0" />
                  수정 전 상태는 버전 기록에 자동 저장됩니다.
                </div>
              )}
            </>
          )}
        </div>

        <DialogFooter className="border-t px-7 py-5">
          {result ? (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setResult(null);
                  setStatusMessage("");
                }}
              >
                다시 검수
              </Button>
              <Button type="button" onClick={() => setOpen(false)}>
                닫기
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                disabled={isReviewing}
                onClick={() => setOpen(false)}
              >
                취소
              </Button>
              <Button
                type="button"
                disabled={isReviewing}
                data-testid="quality-review-confirm"
                onClick={startReview}
              >
                {isReviewing && <Loader2 className="h-4 w-4 animate-spin" />}
                {isReviewing
                  ? "검수 중"
                  : mode === "analyze_and_fix"
                    ? "검수하고 수정하기"
                    : "검수 시작"}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
