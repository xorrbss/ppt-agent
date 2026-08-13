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
  type AuthoredQualityReviewMetrics,
  type AuthoredQualityReviewResult,
  type AuthoredReviewRepairPath,
} from "../../services/api/authored";

const POLL_INTERVAL_MS = 2_000;
const REVIEW_TIMEOUT_MS = 30 * 60 * 1_000;
const MAX_POLL_ERRORS = 4;
const REVIEW_TASK_STORAGE_PREFIX = "presenton:authored-quality-review:task:";

type ReviewScope = "all" | "current";

function slideNumbers(indices?: number[]) {
  if (!indices?.length) return "";
  return indices.map((index) => index + 1).join(", ");
}

function reviewPathLabel(path: AuthoredReviewRepairPath) {
  if (path === "pattern") return "패턴 레시피 재사용";
  if (path === "deterministic") return "개별 결정론적 수정";
  if (path === "llm_repair_plan") return "LLM RepairPlan";
  if (path === "local_patch") return "기존 국소 수정";
  if (path === "full_fallback") return "이전 검수 fallback (현재 자동 사용 안 함)";
  return "변경 없음";
}

function reviewOutcomeLabel(
  attempted: boolean,
  accepted: boolean,
  applied: boolean,
  incomplete = false,
  rejected = false,
) {
  if (applied) return "실제 적용됨";
  if (accepted) return "검증 통과 · 미적용";
  if (rejected) return "검증 거부";
  if (incomplete) return "측정 불완전";
  if (attempted) return "검증 거부";
  return "후보 없음";
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

function measurementLabel(
  measurement: { qid?: string; constraint_id?: string; metric?: string; stage?: string; reason: string },
) {
  const scope = [
    measurement.qid,
    measurement.constraint_id,
    measurement.metric,
    measurement.stage,
  ].filter(Boolean).join(" · ");
  return `${scope ? `${scope}: ` : ""}${measurement.reason}`;
}

function isCleanReviewResult(result: AuthoredQualityReviewResult) {
  return result.remaining_issue_count === 0 &&
    result.unresolved_confirmed_issue_count === 0 &&
    result.incomplete_measurement_count === 0 &&
    result.unattributed_issues.length === 0;
}

function lifecycleSlides(result: AuthoredQualityReviewResult) {
  const hasDetectedIssue = (slide: AuthoredQualityReviewResult["slide_results"][number]) =>
    slide.detected_issues.length > 0 ||
    slide.confirmed_issue_count > 0 ||
    slide.resolved_issues.length > 0 ||
    slide.remaining_issues.length > 0 ||
    slide.incomplete_measurements.length > 0 ||
    slide.publish_blockers.length > 0 ||
    slide.repair_blockers.length > 0 ||
    slide.candidate_rejection_reasons.length > 0 ||
    !!slide.rejection_reason ||
    slide.attempted;
  return [
    {
      id: "detected",
      label: "Detected",
      slides: result.slide_results.filter(hasDetectedIssue),
      description: (slide: AuthoredQualityReviewResult["slide_results"][number]) =>
        slide.detected_issues.join(", ") ||
        (slide.confirmed_issue_count > 0
          ? `Confirmed physical issues: ${slide.confirmed_issue_count}`
          : "") ||
        slide.remaining_issues.join(", ") ||
        slide.resolved_issues.join(", ") ||
        "Detector reported an issue; exact reason was not supplied.",
    },
    {
      id: "corrected-false-positives",
      label: "Corrected false positives",
      slides: result.slide_results.filter((slide) => slide.corrected_false_positive),
      description: (slide: AuthoredQualityReviewResult["slide_results"][number]) =>
        slide.correction_reason || "Human review marked this detector hit as not requiring a repair.",
    },
    {
      id: "incomplete",
      label: "Incomplete measurement",
      slides: result.slide_results.filter(
        (slide) =>
          slide.incomplete_measurements.length > 0 ||
          slide.publish_blockers.length > 0,
      ),
      description: (slide: AuthoredQualityReviewResult["slide_results"][number]) =>
        [
          ...slide.incomplete_measurements,
          ...slide.publish_blockers,
        ].map(measurementLabel).join(", ") || "Scoped measurement is incomplete.",
    },
    {
      id: "candidates",
      label: "Candidates",
      slides: result.slide_results.filter((slide) => slide.candidate_count > 0),
      description: (slide: AuthoredQualityReviewResult["slide_results"][number]) =>
        `${reviewPathLabel(slide.path)} · ${slide.accepted ? "accepted" : "awaiting or rejected"}`,
    },
    {
      id: "rejected",
      label: "Rejected",
      slides: result.slide_results.filter(
        (slide) => slide.candidate_count > 0 && (slide.repair_blockers.length > 0 || slide.candidate_rejection_reasons.length > 0 || (!slide.accepted && !!slide.rejection_reason)),
      ),
      description: (slide: AuthoredQualityReviewResult["slide_results"][number]) =>
        slide.repair_blockers.join(", ") || slide.candidate_rejection_reasons.join(", ") || slide.rejection_reason || "Candidate did not pass acceptance.",
    },
    {
      id: "no-candidate",
      label: "No candidate / preflight",
      slides: result.slide_results.filter(
        (slide) => slide.attempted && slide.candidate_count === 0 && !slide.corrected_false_positive,
      ),
      description: (slide: AuthoredQualityReviewResult["slide_results"][number]) =>
        slide.repair_blockers.join(", ") || slide.rejection_reason || "Preflight or proposal generation produced no candidate.",
    },
    {
      id: "applied",
      label: "Applied",
      slides: result.slide_results.filter((slide) => slide.applied),
      description: (slide: AuthoredQualityReviewResult["slide_results"][number]) =>
        `${slide.apply_status} · publish ${slide.publish_status} · post-verify ${slide.post_verify_status}${slide.rolled_back ? " · rolled back" : ""}`,
    },
    {
      id: "remaining",
      label: "Remaining",
      slides: result.slide_results.filter(
        (slide) =>
          slide.remaining_issues.length > 0 ||
          slide.unresolved_confirmed_issue_count > 0 ||
          (hasDetectedIssue(slide) && !slide.applied && !slide.accepted),
      ),
      description: (slide: AuthoredQualityReviewResult["slide_results"][number]) =>
        slide.remaining_issues.join(", ") ||
        (slide.unresolved_confirmed_issue_count > 0
          ? `Unresolved confirmed issues: ${slide.unresolved_confirmed_issue_count}`
          : "") ||
        slide.rejection_reason ||
        "No accepted, applied repair is recorded for this detected issue.",
    },
  ];
}

function OpsDashboard({ metrics }: { metrics: AuthoredQualityReviewMetrics }) {
  const latency = metrics.latency_percentiles;
  const usage = metrics.stage_usage;
  const latencyStages = [
    ...new Set([
      ...Object.keys(latency?.cold ?? {}),
      ...Object.keys(latency?.warm ?? {}),
    ]),
  ];
  const usageStages = Object.keys(usage ?? {});
  if (!latencyStages.length && !usageStages.length) return null;

  const formatPercentile = (value?: number) =>
    value === undefined ? "—" : formatDuration(value) ?? "—";
  const formatUsage = (value?: number) =>
    value === undefined ? "—" : value.toLocaleString("en-US");
  const formatCost = (value?: number) =>
    value === undefined ? "—" : `$${value.toFixed(4)}`;

  return (
    <section
      data-testid="quality-operations-dashboard"
      className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 text-xs text-slate-700"
    >
      <div>
        <p className="font-semibold text-slate-900">Operations dashboard</p>
        <p className="mt-1 text-slate-600">
          Percentiles are shown only when the backend supplies measured replay samples.
        </p>
      </div>
      {latencyStages.length > 0 && (
        <div className="overflow-x-auto">
          <p className="mb-2 font-medium text-slate-900">Cold / warm latency</p>
          <table className="w-full min-w-[620px] text-left">
            <thead className="border-b text-slate-500">
              <tr>
                <th className="pb-1 pr-3">Stage</th>
                <th className="pb-1 pr-3">Cold p50</th>
                <th className="pb-1 pr-3">Cold p95</th>
                <th className="pb-1 pr-3">Cold p99</th>
                <th className="pb-1 pr-3">Warm p50</th>
                <th className="pb-1 pr-3">Warm p95</th>
                <th className="pb-1">Warm p99</th>
              </tr>
            </thead>
            <tbody>
              {latencyStages.map((stage) => (
                <tr key={stage} className="border-b last:border-0">
                  <td className="py-1.5 pr-3 font-medium">{stage}</td>
                  <td className="py-1.5 pr-3">{formatPercentile(latency?.cold?.[stage]?.p50_ms)}</td>
                  <td className="py-1.5 pr-3">{formatPercentile(latency?.cold?.[stage]?.p95_ms)}</td>
                  <td className="py-1.5 pr-3">{formatPercentile(latency?.cold?.[stage]?.p99_ms)}</td>
                  <td className="py-1.5 pr-3">{formatPercentile(latency?.warm?.[stage]?.p50_ms)}</td>
                  <td className="py-1.5 pr-3">{formatPercentile(latency?.warm?.[stage]?.p95_ms)}</td>
                  <td className="py-1.5">{formatPercentile(latency?.warm?.[stage]?.p99_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {usageStages.length > 0 && (
        <div className="overflow-x-auto">
          <p className="mb-2 font-medium text-slate-900">LLM / Chrome / acceptance / post-verify usage</p>
          <table className="w-full min-w-[620px] text-left">
            <thead className="border-b text-slate-500">
              <tr>
                <th className="pb-1 pr-3">Stage</th>
                <th className="pb-1 pr-3">Input tokens</th>
                <th className="pb-1 pr-3">Output tokens</th>
                <th className="pb-1 pr-3">Total tokens</th>
                <th className="pb-1 pr-3">Cost</th>
                <th className="pb-1">Cache hits / misses</th>
              </tr>
            </thead>
            <tbody>
              {usageStages.map((stage) => {
                const row = usage?.[stage];
                return (
                  <tr key={stage} className="border-b last:border-0">
                    <td className="py-1.5 pr-3 font-medium">{stage}</td>
                    <td className="py-1.5 pr-3">{formatUsage(row?.input_tokens)}</td>
                    <td className="py-1.5 pr-3">{formatUsage(row?.output_tokens)}</td>
                    <td className="py-1.5 pr-3">{formatUsage(row?.total_tokens)}</td>
                    <td className="py-1.5 pr-3">{formatCost(row?.cost_usd)}</td>
                    <td className="py-1.5">{formatUsage(row?.cache_hits)} / {formatUsage(row?.cache_misses)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
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
  const [isReviewing, setIsReviewing] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [result, setResult] = useState<AuthoredQualityReviewResult | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    const storageKey = reviewTaskStorageKey(presentationId);
    const taskId = window.sessionStorage.getItem(storageKey);

    setResult(null);
    setStatusMessage("");
    if (!taskId) return;

    const restoreCompletedReview = async () => {
      try {
        const task =
          await PresentationGenerationApi.getGenerationStatus(taskId);
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
            // Quality review has one maximum-repair mode. The backend may use
            // pattern, deterministic, and bounded typed LLM plans internally;
            // users never select a lower-safety or legacy branch here.
            slidespec_opt_in: true,
          },
        );
      const taskId = started?.id;
      if (!taskId) throw new Error("검수 작업을 시작하지 못했습니다.");
      window.sessionStorage.setItem(
        reviewTaskStorageKey(presentationId),
        taskId,
      );

      const deadline = Date.now() + REVIEW_TIMEOUT_MS;
      let pollErrors = 0;
      while (Date.now() < deadline) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, POLL_INTERVAL_MS),
        );

        try {
          const task =
            await PresentationGenerationApi.getGenerationStatus(taskId);
          pollErrors = 0;
          if (task?.message) setStatusMessage(task.message);

          if (task?.status === "completed") {
            const nextResult = normalizeAuthoredQualityReviewResult(task.data);
            setResult(nextResult);
            if (nextResult.fixed_count > 0) {
              onReload?.();
            }
            notify.success(
              "고품질 검수가 완료되었습니다",
              nextResult.fixed_count > 0
                ? `${nextResult.fixed_count}개 슬라이드를 수정했습니다.`
                : nextResult.issue_slide_count > 0
                  ? `${nextResult.issue_slide_count}개 슬라이드에서 문제를 확인했습니다.`
                  : "수정이 필요한 시각적 문제가 없습니다.",
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
      throw new Error(
        "검수 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
      );
    } catch (error) {
      console.error("Error reviewing AI-authored presentation", error);
      notify.error(
        "고품질 검수 실패",
        error instanceof Error ? error.message : "검수 중 오류가 발생했습니다.",
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
            저장된 AI 저작 슬라이드를 다시 렌더링해 글자 잘림, 요소 겹침, 정렬,
            대비 문제를 검사합니다.
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
              <div
                data-testid="quality-review-summary"
                className={`flex items-start gap-3 rounded-2xl border p-4 ${
                  isCleanReviewResult(result)
                    ? "border-emerald-200 bg-emerald-50"
                    : "border-amber-200 bg-amber-50"
                }`}
              >
                {isCleanReviewResult(result) ? (
                  <BadgeCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
                ) : (
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
                )}
                <div>
                  <p className={`font-semibold ${isCleanReviewResult(result) ? "text-emerald-950" : "text-amber-950"}`}>
                    {isCleanReviewResult(result) ? "검수 완료 · clean" : "검수 완료 · 후속 조치 필요"}
                  </p>
                  <p className={`mt-1 text-sm leading-6 ${isCleanReviewResult(result) ? "text-emerald-900" : "text-amber-900"}`}>
                    {result.reviewed_count}개 슬라이드를 검사했고{" "}
                    {result.issue_slide_count}개에서 문제를 확인했습니다.
                    {result.fixed_count > 0 &&
                      ` ${result.fixed_count}개를 자동 수정했습니다.`}
                  </p>
                  <p className="mt-1 text-xs">
                    문제 슬라이드 {result.issue_slide_count}장 · 상세 이슈 {result.detailed_issue_count}개 · 수정 슬라이드 {result.fixed_count}장 · 해결 이슈 {result.resolved_issue_count}개 · 잔여 이슈 {result.remaining_issue_count}개
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
                    재검증 후 잔여 상세 이슈 {result.remaining_issue_count}개가{" "}
                    {result.remaining_slide_count}개 슬라이드에 남았습니다
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
              {(result.metrics.wall_time_ms !== undefined ||
                result.metrics.total_ms !== undefined ||
                result.metrics.aggregate_stage_time_ms !== undefined ||
                Object.keys(result.metrics.stage_timings_ms).length > 0 ||
                result.metrics.cache_hits !== undefined ||
                result.metrics.cache_misses !== undefined ||
                result.metrics.llm_eligible !== undefined ||
                result.metrics.llm_calls !== undefined ||
                result.metrics.llm_responses !== undefined ||
                result.metrics.llm_valid_plans !== undefined ||
                result.metrics.llm_schema_passes !== undefined ||
                result.metrics.llm_provider_failures !== undefined ||
                result.metrics.llm_empty !== undefined ||
                result.metrics.llm_schema_rejected !== undefined ||
                result.metrics.proposal_preflight_unprojectable !== undefined ||
                result.metrics.llm_preflight_passes !== undefined ||
                result.metrics.llm_rendered !== undefined ||
                result.metrics.llm_negative_cache_hits !== undefined ||
                result.metrics.llm_circuit_open !== undefined ||
                result.metrics.llm_no_candidate_reasons !== undefined ||
                result.metrics.deterministic_resolved !== undefined ||
                result.metrics.vqa_calls !== undefined ||
                result.metrics.patch_count !== undefined ||
                result.metrics.fallback_count !== undefined ||
                result.metrics.patch_attempted !== undefined ||
                result.metrics.patch_succeeded !== undefined ||
                result.metrics.patch_failed !== undefined ||
                result.metrics.fallback_attempted !== undefined ||
                result.metrics.fallback_succeeded !== undefined ||
                result.metrics.fallback_failed !== undefined ||
                result.metrics.candidate_proposed !== undefined ||
                result.metrics.candidate_rendered !== undefined ||
                result.metrics.candidate_accepted !== undefined ||
                result.metrics.validation_passed !== undefined ||
                result.metrics.validation_rejected !== undefined ||
                result.metrics.validation_inconclusive !== undefined ||
                result.metrics.apply_committed !== undefined ||
                result.metrics.publish_active !== undefined ||
                result.metrics.postverify_passed !== undefined ||
                result.metrics.applied !== undefined ||
                result.metrics.rolled_back !== undefined ||
                result.metrics.solver_total_ms !== undefined ||
                result.metrics.solver_branches_evaluated !== undefined ||
                result.metrics.solver_unknown_hard !== undefined ||
                result.metrics.solver_unsat_core_size !== undefined ||
                result.metrics.solver_search_complete !== undefined ||
                result.metrics.kill_switch_active !== undefined ||
                result.metrics.ocr_executed !== undefined ||
                result.metrics.ocr_calls !== undefined ||
                result.metrics.ocr_cache_hits !== undefined ||
                result.metrics.ocr_total_ms !== undefined ||
                result.metrics.corrected_false_positives !== undefined ||
                result.metrics.true_positive_executor_verified !== undefined ||
                result.metrics.true_positive_pending !== undefined ||
                result.metrics.pattern_cluster_count !== undefined ||
                result.metrics.pattern_representative_count !== undefined ||
                result.metrics.pattern_recipe_count !== undefined ||
                result.metrics.pattern_reuse_attempts !== undefined ||
                result.metrics.pattern_reuse_hits !== undefined ||
                result.metrics.pattern_reuse_applied !== undefined ||
                result.metrics.composite_plan_count !== undefined ||
                result.metrics.composite_operations_merged !== undefined ||
                result.metrics.render_candidates_rejected_early !== undefined ||
                result.metrics.full_render_count !== undefined ||
                result.metrics.llm_pattern_call_count !== undefined ||
                result.metrics.individual_fallback_count !== undefined ||
                result.metrics.latency_percentiles !== undefined ||
                result.metrics.stage_usage !== undefined) && (
                <section
                  data-testid="quality-review-metrics"
                  className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs text-slate-700"
                >
                  <p className="font-semibold text-slate-900">검수 처리 내역</p>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                    {(result.metrics.wall_time_ms ?? result.metrics.total_ms) !== undefined && (
                      <span data-testid="quality-wall-time">
                        실제 경과 {formatDuration(result.metrics.wall_time_ms ?? result.metrics.total_ms!) }
                      </span>
                    )}
                    {result.metrics.aggregate_stage_time_ms !== undefined && (
                      <span data-testid="quality-stage-aggregate-time">
                        단계 누적 {formatDuration(result.metrics.aggregate_stage_time_ms)} (병렬 합산)
                      </span>
                    )}
                    {result.metrics.cache_hits !== undefined && (
                      <span>캐시 적중 {result.metrics.cache_hits}</span>
                    )}
                    {result.metrics.cache_misses !== undefined && (
                      <span>캐시 미스 {result.metrics.cache_misses}</span>
                    )}
                    {result.metrics.llm_eligible !== undefined && (
                      <span data-testid="quality-llm-eligible">LLM 대상 {result.metrics.llm_eligible}</span>
                    )}
                    {result.metrics.llm_calls !== undefined && (
                      <span data-testid="quality-llm-calls">LLM 호출 {result.metrics.llm_calls}</span>
                    )}
                    {result.metrics.llm_responses !== undefined && (
                      <span data-testid="quality-llm-responses">LLM 응답 {result.metrics.llm_responses}</span>
                    )}
                    {result.metrics.llm_valid_plans !== undefined && (
                      <span data-testid="quality-llm-valid-plans">유효 RepairPlan {result.metrics.llm_valid_plans}</span>
                    )}
                    {result.metrics.llm_schema_passes !== undefined && (
                      <span data-testid="quality-llm-schema-passes">LLM schema 통과 {result.metrics.llm_schema_passes}</span>
                    )}
                    {result.metrics.llm_provider_failures !== undefined && (
                      <span data-testid="quality-llm-provider-failures">LLM provider 실패 {result.metrics.llm_provider_failures}</span>
                    )}
                    {result.metrics.llm_empty !== undefined && (
                      <span data-testid="quality-llm-empty">LLM 빈 응답 {result.metrics.llm_empty}</span>
                    )}
                    {result.metrics.llm_schema_rejected !== undefined && (
                      <span data-testid="quality-llm-schema-rejected">LLM schema 거부 {result.metrics.llm_schema_rejected}</span>
                    )}
                    {result.metrics.proposal_preflight_unprojectable !== undefined && (
                      <span data-testid="quality-llm-preflight-unprojectable">투영 preflight 차단 {result.metrics.proposal_preflight_unprojectable}</span>
                    )}
                    {result.metrics.llm_preflight_passes !== undefined && (
                      <span data-testid="quality-llm-preflight-passes">투영 preflight 통과 {result.metrics.llm_preflight_passes}</span>
                    )}
                    {result.metrics.llm_rendered !== undefined && (
                      <span data-testid="quality-llm-rendered">LLM 후보 렌더 {result.metrics.llm_rendered}</span>
                    )}
                    {result.metrics.llm_negative_cache_hits !== undefined && (
                      <span data-testid="quality-llm-negative-cache-hits">LLM negative cache {result.metrics.llm_negative_cache_hits}</span>
                    )}
                    {result.metrics.llm_circuit_open !== undefined && (
                      <span data-testid="quality-llm-circuit-open">LLM circuit open {result.metrics.llm_circuit_open}</span>
                    )}
                    {result.metrics.deterministic_resolved !== undefined && (
                      <span>
                        결정론적 해결 {result.metrics.deterministic_resolved}
                      </span>
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
                      <span>
                        국소 패치 시도 {result.metrics.patch_attempted}
                      </span>
                    )}
                    {result.metrics.patch_succeeded !== undefined && (
                      <span>
                        국소 후보 생성 {result.metrics.patch_succeeded}
                      </span>
                    )}
                    {result.metrics.patch_failed !== undefined && (
                      <span>국소 패치 실패 {result.metrics.patch_failed}</span>
                    )}
                    {result.metrics.fallback_attempted !== undefined && (
                      <span>
                        개별 fallback 시도 {result.metrics.fallback_attempted}
                      </span>
                    )}
                    {result.metrics.fallback_succeeded !== undefined && (
                      <span>
                        개별 fallback 후보 생성 {result.metrics.fallback_succeeded}
                      </span>
                    )}
                    {result.metrics.fallback_failed !== undefined && (
                      <span>
                        개별 fallback 실패 {result.metrics.fallback_failed}
                      </span>
                    )}
                    {result.metrics.pattern_cluster_count !== undefined && (
                      <span>패턴 군집 {result.metrics.pattern_cluster_count}</span>
                    )}
                    {result.metrics.pattern_representative_count !== undefined && (
                      <span>대표 표본 {result.metrics.pattern_representative_count}</span>
                    )}
                    {result.metrics.pattern_recipe_count !== undefined && (
                      <span>레시피 {result.metrics.pattern_recipe_count}</span>
                    )}
                    {result.metrics.pattern_reuse_attempts !== undefined && (
                      <span>패턴 재사용 시도 {result.metrics.pattern_reuse_attempts}</span>
                    )}
                    {result.metrics.pattern_reuse_hits !== undefined && (
                      <span>패턴 재사용 적중 {result.metrics.pattern_reuse_hits}</span>
                    )}
                    {result.metrics.pattern_reuse_applied !== undefined && (
                      <span>패턴 재사용 적용 {result.metrics.pattern_reuse_applied}</span>
                    )}
                    {result.metrics.composite_plan_count !== undefined && (
                      <span>복합 RepairPlan {result.metrics.composite_plan_count}</span>
                    )}
                    {result.metrics.composite_operations_merged !== undefined && (
                      <span>병합 연산 {result.metrics.composite_operations_merged}</span>
                    )}
                    {result.metrics.render_candidates_rejected_early !== undefined && (
                      <span>조기 렌더 거부 {result.metrics.render_candidates_rejected_early}</span>
                    )}
                    {result.metrics.full_render_count !== undefined && (
                      <span>전체 렌더 {result.metrics.full_render_count}</span>
                    )}
                    {result.metrics.llm_pattern_call_count !== undefined && (
                      <span>LLM RepairPlan {result.metrics.llm_pattern_call_count}</span>
                    )}
                    {result.metrics.individual_fallback_count !== undefined && (
                      <span>개별 경로 전환 {result.metrics.individual_fallback_count}</span>
                    )}
                    {result.metrics.candidate_proposed !== undefined && (
                      <span data-testid="quality-candidate-count">
                        후보 {result.metrics.candidate_proposed}
                      </span>
                    )}
                    {result.metrics.candidate_rendered !== undefined && (
                      <span data-testid="quality-candidate-rendered">
                        후보 렌더 {result.metrics.candidate_rendered}
                      </span>
                    )}
                    {result.metrics.candidate_accepted !== undefined && (
                      <span data-testid="quality-candidate-accepted">
                        후보 채택 {result.metrics.candidate_accepted}
                      </span>
                    )}
                    {result.metrics.validation_passed !== undefined && (
                      <span data-testid="quality-accepted-count">
                        검증 통과 {result.metrics.validation_passed}
                      </span>
                    )}
                    {result.metrics.validation_rejected !== undefined && (
                      <span data-testid="quality-candidate-rejected">
                        후보 거부 {result.metrics.validation_rejected}
                      </span>
                    )}
                    {result.metrics.validation_inconclusive !== undefined && (
                      <span data-testid="quality-candidate-inconclusive">
                        검증 보류 {result.metrics.validation_inconclusive}
                      </span>
                    )}
                    {result.metrics.apply_committed !== undefined && (
                      <span>커밋 {result.metrics.apply_committed}</span>
                    )}
                    {result.metrics.publish_active !== undefined && (
                      <span>활성 발행 {result.metrics.publish_active}</span>
                    )}
                    {result.metrics.postverify_passed !== undefined && (
                      <span>사후 검증 {result.metrics.postverify_passed}</span>
                    )}
                    {result.metrics.applied !== undefined && (
                      <span data-testid="quality-applied-count">
                        실제 적용 {result.metrics.applied}
                      </span>
                    )}
                    {result.metrics.rolled_back !== undefined && (
                      <span data-testid="quality-rollback-count">
                        롤백 {result.metrics.rolled_back}
                      </span>
                    )}
                    {result.metrics.corrected_false_positives !== undefined && (
                      <span data-testid="quality-corrected-false-positive-count">
                        Corrected false positives {result.metrics.corrected_false_positives}
                      </span>
                    )}
                    {result.metrics.true_positive_executor_verified !== undefined && (
                      <span data-testid="quality-true-positive-verified-count">
                        Executor-verified true positives {result.metrics.true_positive_executor_verified}
                      </span>
                    )}
                    {result.metrics.true_positive_pending !== undefined && (
                      <span data-testid="quality-true-positive-pending-count">
                        True positives pending evidence {result.metrics.true_positive_pending}
                      </span>
                    )}
                    {result.metrics.solver_total_ms !== undefined && (
                      <span data-testid="quality-solver-elapsed">
                        Solver {formatDuration(result.metrics.solver_total_ms)}
                      </span>
                    )}
                    {result.metrics.solver_branches_evaluated !== undefined && (
                      <span data-testid="quality-solver-branches">
                        Solver branches {result.metrics.solver_branches_evaluated}
                      </span>
                    )}
                    {result.metrics.solver_unknown_hard !== undefined && (
                      <span data-testid="quality-solver-unknown">
                        Solver unknown {result.metrics.solver_unknown_hard}
                      </span>
                    )}
                    {result.metrics.solver_unsat_core_size !== undefined && (
                      <span data-testid="quality-solver-unsat-core">
                        UNSAT core {result.metrics.solver_unsat_core_size}
                      </span>
                    )}
                    {result.metrics.solver_search_complete !== undefined && (
                      <span data-testid="quality-solver-search-state">
                        {result.metrics.solver_search_complete
                          ? "Solver search complete"
                          : "Solver search bounded"}
                      </span>
                    )}
                    {result.metrics.kill_switch_active !== undefined && (
                      <span data-testid="quality-kill-switch-state">
                        {result.metrics.kill_switch_active
                          ? "SlideSpec kill switch active"
                          : "SlideSpec kill switch inactive"}
                      </span>
                    )}
                    {result.metrics.ocr_executed !== undefined && (
                      <span>
                        {result.metrics.ocr_executed
                          ? "OCR 실행됨"
                          : "OCR 미실행"}
                      </span>
                    )}
                    {result.metrics.ocr_calls !== undefined && (
                      <span>OCR 호출 {result.metrics.ocr_calls}</span>
                    )}
                    {result.metrics.ocr_cache_hits !== undefined && (
                      <span>OCR 캐시 적중 {result.metrics.ocr_cache_hits}</span>
                    )}
                    {result.metrics.ocr_total_ms !== undefined && (
                      <span>
                        OCR 총 {formatDuration(result.metrics.ocr_total_ms)}
                      </span>
                    )}
                  </div>
                  {result.metrics.llm_no_candidate_reasons && result.metrics.llm_no_candidate_reasons.length > 0 && (
                    <p data-testid="quality-llm-no-candidate-reasons" className="mt-2 text-amber-800">
                      LLM 후보 미생성 사유: {result.metrics.llm_no_candidate_reasons.join(", ")}
                    </p>
                  )}
                  {Object.keys(result.metrics.stage_timings_ms).length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-slate-600">
                      {Object.entries(result.metrics.stage_timings_ms).map(
                        ([stage, duration]) => (
                          <span key={stage}>
                            {stage}: {formatDuration(duration)}
                          </span>
                        ),
                      )}
                    </div>
                  )}
                </section>
              )}
              <OpsDashboard metrics={result.metrics} />
              {result.unattributed_issues.length > 0 && (
                <section
                  data-testid="quality-review-unattributed-issues"
                  className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950"
                >
                  <p className="font-semibold">슬라이드 미지정 상세 이슈</p>
                  <p className="mt-1 text-amber-800">
                    측정 불완전과 별개입니다. 슬라이드 인덱스가 없어 이동이나 자동 수정 대상으로 추정하지 않았습니다.
                  </p>
                  <ul className="mt-2 list-disc space-y-1 pl-4">
                    {result.unattributed_issues.map((issue) => (
                      <li key={issue}>{issue}</li>
                    ))}
                  </ul>
                </section>
              )}
              {result.slide_results.length > 0 && (
                <section
                  data-testid="quality-review-lifecycle"
                  className="grid gap-2 sm:grid-cols-2"
                >
                  {lifecycleSlides(result).map((stage) => (
                    <div
                      key={stage.id}
                      data-testid={`quality-lifecycle-${stage.id}`}
                      className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs"
                    >
                      <p className="font-semibold text-slate-900">
                        {stage.label} <span className="text-slate-500">{stage.slides.length}</span>
                      </p>
                      {stage.slides.length > 0 ? (
                        <ul className="mt-1 space-y-1 text-slate-700">
                          {stage.slides.map((slide) => (
                            <li key={slide.slide_index}>
                              Slide {slide.slide_index + 1}: {stage.description(slide)}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-1 text-slate-500">None</p>
                      )}
                    </div>
                  ))}
                </section>
              )}
              {result.slide_results.length > 0 && (
                <section
                  data-testid="quality-review-details"
                  className="space-y-2"
                >
                  <p className="text-sm font-semibold text-[#191919]">
                    슬라이드별 변경
                  </p>
                  {result.slide_results.map((slide) => (
                    <details
                      key={slide.slide_index}
                      open
                      data-testid={`quality-review-slide-${slide.slide_index}`}
                      className="rounded-xl border border-[#E3E4E8] bg-white px-4 py-3 text-sm"
                    >
                      <summary className="cursor-pointer font-medium text-[#191919]">
                        <span className="mr-1 font-semibold">
                          {reviewOutcomeLabel(
                            slide.attempted,
                            slide.accepted,
                            slide.applied,
                          )} ·
                        </span>
                        {slide.slide_index + 1}번 슬라이드 ·{" "}
                        {reviewPathLabel(slide.path)}
                      </summary>
                      <div className="mt-3 space-y-2 text-xs text-gray-700">
                        <p className="rounded-lg border border-slate-200 bg-slate-50 p-2 text-slate-700">
                          Apply: {slide.apply_status} · Publish: {slide.publish_status} · Post-verify: {slide.post_verify_status} · Rollback: {slide.rolled_back ? "yes" : "no"}
                        </p>
                        {slide.detected_issues.length > 0 && (
                          <p className="text-slate-700">
                            Detected: {slide.detected_issues.join(" · ")}
                          </p>
                        )}
                        {slide.confirmed_issue_count > 0 && (
                          <p data-testid={`quality-confirmed-issues-${slide.slide_index}`} className="text-amber-800">
                            Confirmed physical issues: {slide.confirmed_issue_count} · unresolved: {slide.unresolved_confirmed_issue_count}
                          </p>
                        )}
                        {slide.evidence_atoms.length > 0 && (
                          <p className="break-words text-slate-600">
                            Evidence: {slide.evidence_atoms.map((atom) =>
                              [atom.qid, atom.constraint_id, atom.metric, atom.stage, atom.state]
                                .filter(Boolean)
                                .join(" · "),
                            ).join(" | ")}
                          </p>
                        )}
                        {slide.incomplete_measurements.length > 0 && (
                          <div data-testid={`quality-incomplete-${slide.slide_index}`} className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-amber-950">
                            <p className="font-medium">Incomplete measurement</p>
                            <ul className="mt-1 list-disc space-y-1 pl-4">
                              {slide.incomplete_measurements.map((measurement, index) => (
                                <li key={`${measurementLabel(measurement)}-${index}`}>
                                  {measurementLabel(measurement)}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {slide.publish_blockers.length > 0 && (
                          <div data-testid={`quality-publish-blocker-${slide.slide_index}`} className="rounded-lg border border-rose-200 bg-rose-50 p-2 text-rose-950">
                            <p className="font-medium">Publish blocked by dependency-cone evidence</p>
                            <ul className="mt-1 list-disc space-y-1 pl-4">
                              {slide.publish_blockers.map((blocker, index) => (
                                <li key={`${measurementLabel(blocker)}-${index}`}>
                                  {measurementLabel(blocker)}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {slide.repair_blockers.length > 0 && (
                          <div data-testid={`quality-repair-blocker-${slide.slide_index}`} className="rounded-lg border border-rose-200 bg-rose-50 p-2 text-rose-950">
                            <p className="font-medium">Repair blocked by scoped geometry evidence</p>
                            <ul className="mt-1 list-disc space-y-1 pl-4">
                              {slide.repair_blockers.map((blocker) => (
                                <li key={blocker}>{blocker}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {slide.corrected_false_positive && (
                          <p className="rounded-lg border border-sky-200 bg-sky-50 p-2 text-sky-900">
                            Corrected detector false positive — source unchanged.
                            {slide.correction_reason ? ` ${slide.correction_reason}` : ""}
                          </p>
                        )}
                        {slide.attempted &&
                          !slide.accepted &&
                          !slide.applied &&
                          slide.rejection_reason && (
                            <p className="text-amber-700">
                              미채택 사유: {slide.rejection_reason}
                            </p>
                          )}
                        {slide.ocr && (
                          <div className="rounded-lg border border-sky-100 bg-sky-50 p-3 text-sky-950">
                            <p className="font-medium">
                              {ocrStatusLabel(slide.ocr)}
                            </p>
                            {slide.ocr.executed && (
                              <>
                                <p className="mt-1">
                                  OCR 비교: baseline → candidate
                                </p>
                                {slide.ocr.baseline && (
                                  <p className="mt-1">
                                    기준 누락:{" "}
                                    {ocrOmissionLabel(slide.ocr.baseline)}
                                  </p>
                                )}
                                {slide.ocr.candidate && (
                                  <p className="mt-1">
                                    후보 누락:{" "}
                                    {ocrOmissionLabel(slide.ocr.candidate)}
                                  </p>
                                )}
                                {(slide.ocr.comparison?.accepted === false ||
                                  slide.ocr.comparison?.no_regression ===
                                    false) && (
                                  <p className="mt-1 text-amber-700">
                                    후보 판정: OCR 회귀로 미채택
                                  </p>
                                )}
                                {slide.ocr.comparison?.reason && (
                                  <p className="mt-1 text-amber-700">
                                    OCR 판정 사유: {slide.ocr.comparison.reason}
                                  </p>
                                )}
                                {slide.ocr.comparison?.resolved && (
                                  <p className="mt-1 text-emerald-700">
                                    해결된 OCR 누락:{" "}
                                    {ocrOmissionLabel(
                                      slide.ocr.comparison.resolved,
                                    )}
                                  </p>
                                )}
                                {slide.ocr.comparison?.remaining && (
                                  <p className="mt-1 text-amber-700">
                                    남은 OCR 누락:{" "}
                                    {ocrOmissionLabel(
                                      slide.ocr.comparison.remaining,
                                    )}
                                  </p>
                                )}
                                {slide.ocr.comparison?.regressions && (
                                  <p className="mt-1 text-amber-700">
                                    후보 OCR 회귀:{" "}
                                    {ocrOmissionLabel(
                                      slide.ocr.comparison.regressions,
                                    )}
                                  </p>
                                )}
                              </>
                            )}
                            {!slide.ocr.executed && slide.ocr.reason && (
                              <p className="mt-1 text-amber-700">
                                사유: {slide.ocr.reason}
                              </p>
                            )}
                          </div>
                        )}
                        {slide.element_changes.map((change, index) => (
                          <div
                            key={`${change.element_id ?? "slide"}-${index}`}
                            className="rounded-lg bg-slate-50 p-3"
                          >
                            <p className="font-medium text-slate-900">
                              <span
                                className={
                                  change.applied
                                    ? "text-emerald-700"
                                    : "text-amber-700"
                                }
                              >
                                {change.applied ? "적용된 변경" : "미채택 후보"}{" "}
                                ·
                              </span>
                              요소 {change.element_id ?? "슬라이드 수준"} ·{" "}
                              {reviewPathLabel(change.path)}
                            </p>
                            {!change.applied &&
                              (change.before !== undefined ||
                                change.after !== undefined) && (
                                <p className="mt-1 text-amber-700">
                                  아래 비교값은 실제 슬라이드에 적용되지 않은
                                  미채택 후보입니다.
                                </p>
                              )}
                            {(change.before !== undefined ||
                              change.after !== undefined) && (
                              <p className="mt-1 break-words">
                                전: {change.before ?? "—"} → 후:{" "}
                                {change.after ?? "—"}
                              </p>
                            )}
                            {change.changed_properties.length > 0 && (
                              <p className="mt-1 text-slate-600">
                                Changed properties: {change.changed_properties.join(", ")}
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
                      <span className="block text-sm font-semibold">
                        전체 슬라이드
                      </span>
                      <span className="mt-1 block text-xs text-gray-500">
                        총 {slideCount}개
                      </span>
                    </span>
                  </label>
                  <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-[#E3E4E8] p-4">
                    <RadioGroupItem value="current" />
                    <span>
                      <span className="block text-sm font-semibold">
                        현재 슬라이드
                      </span>
                      <span className="mt-1 block text-xs text-gray-500">
                        {currentSlide + 1}번만 빠르게 검수
                      </span>
                    </span>
                  </label>
                </RadioGroup>
              </fieldset>

              <div className="flex items-center gap-2 rounded-xl bg-[#F6F3FF] px-4 py-3 text-xs text-[#5141E5]">
                <History className="h-4 w-4 shrink-0" />
                문제를 검토한 뒤 자동으로 수정하며, 수정 전 상태는 버전 기록에
                저장됩니다.
              </div>
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
                {isReviewing ? "검수 중" : "검수하고 자동 수정하기"}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
