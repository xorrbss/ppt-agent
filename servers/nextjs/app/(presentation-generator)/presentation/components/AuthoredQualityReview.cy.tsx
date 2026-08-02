import React from "react";

import AuthoredQualityReview from "./AuthoredQualityReview";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { normalizeAuthoredQualityReviewResult } from "../../services/api/authored";

describe("AuthoredQualityReview result details", () => {
  it("restores a completed review from the durable task API after refresh", () => {
    const getGenerationStatus = cy
      .stub(PresentationGenerationApi, "getGenerationStatus")
      .resolves({
        status: "completed",
        message: "저장된 검수 결과입니다.",
        data: {
          reviewed_count: 2,
          issue_slide_count: 1,
          fixed_count: 1,
          fixed_slide_indices: [1],
          remaining_issue_count: 0,
          remaining_slide_indices: [],
          slide_results: [
            {
              slide_index: 1,
              attempted: true,
              applied: true,
              path: "local_patch",
              resolved_issues: ["text overflow"],
              remaining_issues: [],
              element_changes: [
                {
                  element_id: "qid-restored-title",
                  before: "font-size: 42px",
                  after: "font-size: 38px",
                  applied: true,
                  path: "local_patch",
                  resolved_issues: ["text overflow"],
                  remaining_issues: [],
                },
              ],
            },
          ],
        },
      });

    cy.window().then((window) => {
      window.sessionStorage.setItem(
        "presenton:authored-quality-review:task:deck-refresh",
        "quality-refresh-task"
      );
    });
    cy.mount(
      <AuthoredQualityReview
        presentationId="deck-refresh"
        slideCount={2}
        currentSlide={1}
      />
    );

    cy.get('[data-testid="quality-review-trigger"]').click();
    cy.get('[data-testid="quality-review-details"]')
      .should("contain.text", "2번 슬라이드")
      .and("contain.text", "qid-restored-title")
      .and("contain.text", "font-size: 42px")
      .and("contain.text", "text overflow");
    cy.then(() => {
      expect(getGenerationStatus).to.have.been.calledWith("quality-refresh-task");
    });
  });

  it("shows repair truth and OCR execution outcomes without exposing OCR source text", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "reviewAuthoredPresentation")
      .resolves({ id: "quality-task" });
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "completed",
      data: {
        reviewed_count: 8,
        issue_slide_count: 1,
        fixed_count: 1,
        fixed_slide_indices: [2],
        remaining_issue_count: 0,
        remaining_slide_indices: [],
        version_saved: true,
        slide_results: [
          {
            slide_index: 2,
            attempted: true,
            applied: true,
            path: "local_patch",
            resolved_issues: ["text overflow"],
            remaining_issues: [],
            element_changes: [
              {
                element_id: "qid-title",
                before: "font-size: 42px",
                after: "font-size: 38px",
                applied: true,
                path: "local_patch",
                resolved_issues: ["text overflow"],
                remaining_issues: [],
              },
            ],
          },
          {
            slide_index: 5,
            attempted: true,
            applied: false,
            rejection_reason: "OCR candidate regressed",
            path: "local_patch",
            resolved_issues: [],
            remaining_issues: ["text overflow"],
            element_changes: [
              {
                element_id: "qid-body",
                before: "font-size: 28px",
                after: "font-size: 24px",
                applied: false,
                path: "local_patch",
                resolved_issues: [],
                remaining_issues: ["text overflow"],
              },
            ],
          },
        ],
        ocr_checks: [
          {
            slide_index: 2,
            executed: true,
            mode: "strict",
            baseline: { text: ["confidential baseline text"], numbers: ["2025"], symbols: [] },
            candidate: { text: [], numbers: ["2025"], symbols: [] },
            comparison: {
              accepted: true,
              strict_improvement: true,
              no_regression: true,
              resolved: { text: ["confidential baseline text"], numbers: [], symbols: [] },
              remaining: { text: [], numbers: ["2025"], symbols: [] },
            },
          },
          {
            slide_index: 3,
            routed: false,
            skip_reason: "clean slide routed locally",
            mode: "strict",
          },
          {
            slide_index: 4,
            status: "unavailable",
            available: false,
            failure_reason: "OCR service unavailable",
          },
          {
            slide_index: 5,
            executed: true,
            baseline: { text: [], numbers: [], symbols: [] },
            candidate: { text: [], numbers: ["9"], symbols: [] },
            comparison: {
              accepted: false,
              no_regression: false,
              reason: "candidate introduced a number omission",
              regressions: { text: [], numbers: ["9"], symbols: [] },
            },
          },
        ],
        metrics: {
          stage_timings_ms: { local_inspection: 18, detail_vqa: 1_200 },
          total_ms: 1_450,
          cache_hits: 3,
          vqa_calls: 1,
          patch_count: 1,
          fallback_count: 0,
          patch_attempted: 2,
          patch_succeeded: 1,
          patch_failed: 1,
          fallback_attempted: 1,
          fallback_succeeded: 0,
          fallback_failed: 1,
          ocr_executed: true,
          ocr_calls: 2,
          ocr_cache_hits: 1,
          ocr_total_ms: 345,
        },
      },
    });

    cy.mount(
      <AuthoredQualityReview
        presentationId="deck-1"
        slideCount={8}
        currentSlide={2}
      />
    );

    cy.get('[data-testid="quality-review-trigger"]').click();
    cy.get('[data-testid="quality-review-confirm"]').click();
    cy.tick(2_000);

    cy.window().then((window) => {
      expect(
        window.sessionStorage.getItem(
          "presenton:authored-quality-review:task:deck-1"
        )
      ).to.equal("quality-task");
    });

    cy.get('[data-testid="quality-review-metrics"]')
      .should("contain.text", "VQA 호출 1")
      .and("contain.text", "국소 패치 1")
      .and("contain.text", "국소 패치 시도 2")
      .and("contain.text", "전체 재작성 실패 1")
      .and("contain.text", "OCR 실행됨")
      .and("contain.text", "OCR 호출 2")
      .and("contain.text", "OCR 캐시 적중 1")
      .and("contain.text", "OCR 총 345ms")
      .and("contain.text", "local_inspection: 18ms");
    cy.get('[data-testid="quality-review-details"]')
      .should("contain.text", "3번 슬라이드")
      .and("contain.text", "qid-title")
      .and("contain.text", "font-size: 42px")
      .and("contain.text", "text overflow");
    cy.get('[data-testid="quality-review-slide-2"]')
      .should("contain.text", "실제 적용됨")
      .and("contain.text", "적용된 변경")
      .and("contain.text", "OCR 비교: baseline → candidate")
      .and("contain.text", "해결된 OCR 누락: 텍스트 1 · 숫자 0 · 기호 0")
      .and("not.contain.text", "confidential baseline text");
    cy.get('[data-testid="quality-review-slide-3"]')
      .should("contain.text", "OCR 미실행 · 로컬 라우팅으로 생략")
      .and("not.contain.text", "OCR 비교: baseline → candidate");
    cy.get('[data-testid="quality-review-slide-4"]')
      .should("contain.text", "OCR 실행 불가")
      .and("contain.text", "사유: OCR service unavailable");
    cy.get('[data-testid="quality-review-slide-5"]')
      .should("contain.text", "시도했으나 미채택")
      .and("contain.text", "미채택 사유: OCR candidate regressed")
      .and("contain.text", "미채택 후보")
      .and("contain.text", "실제 슬라이드에 적용되지 않은 미채택 후보")
      .and("contain.text", "후보 판정: OCR 회귀로 미채택")
      .and("contain.text", "후보 OCR 회귀: 텍스트 0 · 숫자 1 · 기호 0");
  });

  it("renders telemetry-backed full fallback repairs without exposing OCR payload text", () => {
    cy.stub(PresentationGenerationApi, "reviewAuthoredPresentation")
      .resolves({ id: "quality-fallback-task" });
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "completed",
      data: {
        reviewed_count: 1,
        issue_slide_count: 1,
        fixed_count: 1,
        fixed_slide_indices: [4],
        remaining_issue_count: 0,
        remaining_slide_indices: [],
        version_saved: true,
        telemetry: {
          stage_timings_ms: {
            local_inspection: 12,
            full_fallback: 12_500,
            candidate_vqa: 1_100,
            ocr: 412,
          },
          total_ms: 14_012,
          vqa_calls: 2,
          fallback_count: 1,
          fallback_attempts: 1,
          fallback_successes: 1,
          fallback_failures: 0,
          ocr_executed: true,
          ocr_requests: 2,
          ocr_cache_hits: 1,
          ocr_ms: 412,
        },
        slide_results: [
          {
            slide_index: 4,
            attempted: true,
            applied: true,
            path: "full_fallback",
            resolved_issues: ["broken structure"],
            remaining_issues: [],
            element_changes: [
              {
                element_id: "qid-layout-4",
                before: "invalid nesting",
                after: "valid structure",
                applied: true,
                path: "full_fallback",
                resolved_issues: ["broken structure"],
                remaining_issues: [],
              },
            ],
          },
        ],
        ocr_checks: [
          {
            slide_index: 4,
            status: "executed",
            executed: true,
            available: true,
            baseline: {
              status: "success",
              source_text: "omitted OCR source must never be shown",
            },
            baseline_omissions: { text: 3, numbers: 1, symbols: 1 },
            candidate_omissions: { text: 0, numbers: 0, symbols: 0 },
            comparison: {
              accepted: true,
              strict_improvement: true,
              no_regression: true,
              resolved: { text: 3, numbers: 1, symbols: 1 },
              remaining: { text: 0, numbers: 0, symbols: 0 },
            },
          },
        ],
      },
    });

    cy.mount(
      <AuthoredQualityReview
        presentationId="deck-fallback"
        slideCount={6}
        currentSlide={4}
      />,
    );

    cy.get('[data-testid="quality-review-trigger"]').click();
    cy.get('[data-testid="quality-review-confirm"]').click();

    cy.get('[data-testid="quality-review-metrics"]')
      .should("contain.text", "fallback 1")
      .and("contain.text", "full_fallback:")
      .and("contain.text", "candidate_vqa:")
      .and("contain.text", "ocr:");
    cy.get('[data-testid="quality-review-slide-4"]')
      .should("contain.text", "qid-layout-4")
      .and("contain.text", "invalid nesting")
      .and("contain.text", "valid structure")
      .and("contain.text", "broken structure")
      .and("not.contain.text", "omitted OCR source must never be shown");
  });

  it("normalizes legacy repair details without treating an unlisted candidate as applied", () => {
    const result = normalizeAuthoredQualityReviewResult({
      fixed_slide_indices: [1],
      metrics: {
        patch_attempts: 2,
        patch_successes: 1,
        patch_failures: 1,
        fallback_attempts: 1,
        fallback_successes: 0,
        fallback_failures: 1,
      },
      slide_results: [
        {
          slide_index: 1,
          path: "local_patch",
          element_changes: [
            { element_id: "accepted", path: "local_patch" },
          ],
        },
        {
          slide_index: 2,
          path: "local_patch",
          element_changes: [
            { element_id: "candidate", path: "local_patch" },
          ],
        },
      ],
    });

    expect(result.slide_results[0]).to.include({ attempted: true, applied: true });
    expect(result.slide_results[0].element_changes[0].applied).to.equal(true);
    expect(result.slide_results[1]).to.include({ attempted: true, applied: false });
    expect(result.slide_results[1].element_changes[0].applied).to.equal(false);
    expect(result.metrics).to.include({
      patch_attempted: 2,
      patch_succeeded: 1,
      patch_failed: 1,
      fallback_attempted: 1,
      fallback_succeeded: 0,
      fallback_failed: 1,
    });
  });

  it("normalizes the privacy-safe backend OCR contract", () => {
    const result = normalizeAuthoredQualityReviewResult({
      slide_results: [{ slide_index: 0, path: "none" }],
      ocr_checks: [{
        slide_index: 0,
        status: "executed",
        executed: true,
        available: true,
        baseline: { status: "success", metrics: { deletions: 99 } },
        candidate: { status: "success", metrics: { deletions: 99 } },
        baseline_omissions: { text: 2, numbers: 1, symbols: 0 },
        candidate_omissions: { text: 0, numbers: 0, symbols: 0 },
        comparison: {
          accepted: true,
          strict_improvement: true,
          no_regression: true,
          resolved: { text: 2, numbers: 1, symbols: 0 },
        },
      }],
    });

    expect(result.slide_results[0].ocr).to.deep.include({
      status: "executed",
      executed: true,
      baseline: { text: 2, numbers: 1, symbols: 0 },
      candidate: { text: 0, numbers: 0, symbols: 0 },
    });
    expect(result.slide_results[0].ocr?.comparison?.resolved).to.deep.equal({
      text: 2,
      numbers: 1,
      symbols: 0,
    });
  });
});
