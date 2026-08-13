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
    const reviewAuthoredPresentation = cy.stub(PresentationGenerationApi, "reviewAuthoredPresentation")
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

    cy.then(() => {
      expect(reviewAuthoredPresentation).to.have.been.calledWith("deck-1", {
        scope: "all",
        slide_indices: [],
        slidespec_opt_in: true,
      });
    });
    cy.get('[data-testid="slidespec-canary-opt-in"]').should("not.exist");

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
      .and("contain.text", "개별 fallback 실패 1")
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
      .should("contain.text", "검증 거부")
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

  it("renders bounded SlideSpec solver, candidate, rollback, and kill-switch telemetry", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "reviewAuthoredPresentation")
      .resolves({ id: "quality-telemetry-task" });
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "completed",
      data: {
        reviewed_count: 1,
        issue_slide_count: 1,
        fixed_count: 0,
        fixed_slide_indices: [],
        remaining_issue_count: 1,
        remaining_slide_indices: [0],
        metrics: {
          solver: {
            elapsed_ms: 48,
            branches: 17,
            unknown: 2,
            unsat_core_size: 3,
            search_complete: false,
          },
          candidates: { proposed: 4, rendered: 3, accepted: 1, rejected: 2 },
          transaction: { rollback_count: 1 },
          rollout: { kill_switch_enabled: true },
        },
      },
    });

    cy.mount(
      <AuthoredQualityReview
        presentationId="deck-telemetry"
        slideCount={1}
        currentSlide={0}
      />,
    );

    cy.get('[data-testid="quality-review-trigger"]').click();
    cy.get('[data-testid="quality-review-confirm"]').click();
    cy.tick(2_000);
    cy.get('[data-testid="quality-review-metrics"]')
      .should("contain.text", "Solver 48ms")
      .and("contain.text", "SlideSpec kill switch active");
    cy.get('[data-testid="quality-solver-branches"]').should(
      "contain.text",
      "17",
    );
    cy.get('[data-testid="quality-solver-unknown"]').should(
      "contain.text",
      "2",
    );
    cy.get('[data-testid="quality-solver-unsat-core"]').should(
      "contain.text",
      "3",
    );
    cy.get('[data-testid="quality-solver-search-state"]').should(
      "contain.text",
      "bounded",
    );
    cy.get('[data-testid="quality-candidate-count"]').should("contain.text", "4");
    cy.get('[data-testid="quality-candidate-rendered"]').should("contain.text", "3");
    cy.get('[data-testid="quality-candidate-accepted"]').should("contain.text", "1");
    cy.get('[data-testid="quality-candidate-rejected"]').should("contain.text", "2");
    cy.get('[data-testid="quality-rollback-count"]').should("contain.text", "1");
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

  it("separates repair lifecycle evidence and renders measured operations telemetry", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "reviewAuthoredPresentation")
      .resolves({ id: "quality-operations-task" });
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "completed",
      data: {
        reviewed_count: 4,
        issue_slide_count: 4,
        fixed_count: 1,
        fixed_slide_indices: [0],
        remaining_issue_count: 2,
        remaining_slide_indices: [1, 3],
        slide_results: [
          {
            slide_index: 0,
            attempted: true,
            candidate_count: 1,
            accepted: true,
            applied: true,
            apply_status: "committed",
            publish_status: "active",
            post_verify_status: "passed",
            rolled_back: false,
            detected_issues: ["text overflow"],
            resolved_issues: ["text overflow"],
            element_changes: [{
              element_id: "title",
              before: "font-size: 42px",
              after: "font-size: 38px",
              changed_properties: ["font-size", "line-height"],
              applied: true,
              resolved_issues: ["text overflow"],
              remaining_issues: [],
              path: "local_patch",
            }],
            path: "local_patch",
          },
          {
            slide_index: 1,
            attempted: true,
            candidate_count: 1,
            accepted: false,
            applied: false,
            rejection_reason: "target issue not resolved",
            detected_issues: ["overlap"],
            remaining_issues: ["overlap"],
            path: "local_patch",
          },
          {
            slide_index: 2,
            attempted: false,
            accepted: false,
            applied: false,
            corrected_false_positive: true,
            correction_reason: "intentional overlap; source unchanged",
            detected_issues: ["protected image crop"],
            remaining_issues: [],
            path: "none",
          },
          {
            slide_index: 3,
            attempted: true,
            accepted: false,
            applied: false,
            rejection_reason: "proposal preflight unavailable",
            detected_issues: ["unmeasured geometry"],
            remaining_issues: ["unmeasured geometry"],
            path: "none",
          },
        ],
        metrics: {
          latency_percentiles: {
            cold: { deck_total: { p50_ms: 120, p95_ms: 180, p99_ms: 220 } },
            warm: { deck_total: { p50_ms: 40, p95_ms: 60, p99_ms: 75 } },
          },
          stage_usage: {
            llm: { input_tokens: 120, output_tokens: 30, total_tokens: 150, cost_usd: 0.0123, cache_hits: 1, cache_misses: 2 },
            chrome: { cache_hits: 3, cache_misses: 1 },
            acceptance: { total_tokens: 20, cost_usd: 0.001 },
            postverify: { cache_hits: 1, cache_misses: 0 },
          },
          corrected_false_positives: 18,
          true_positive_executor_verified: 0,
          true_positive_pending: 5,
        },
      },
    });

    cy.mount(<AuthoredQualityReview presentationId="deck-operations" slideCount={3} />);
    cy.get('[data-testid="quality-review-trigger"]').click();
    cy.get('[data-testid="quality-review-confirm"]').click();
    cy.tick(2_000);

    cy.get('[data-testid="quality-lifecycle-detected"]').should("contain.text", "4");
    cy.get('[data-testid="quality-lifecycle-candidates"]').should("contain.text", "2");
    cy.get('[data-testid="quality-lifecycle-rejected"]').should("contain.text", "target issue not resolved");
    cy.get('[data-testid="quality-lifecycle-no-candidate"]')
      .should("contain.text", "Slide 4")
      .and("contain.text", "proposal preflight unavailable");
    cy.get('[data-testid="quality-lifecycle-applied"]').should("contain.text", "Slide 1");
    cy.get('[data-testid="quality-lifecycle-remaining"]').should("contain.text", "Slide 2");
    cy.get('[data-testid="quality-lifecycle-corrected-false-positives"]')
      .should("contain.text", "intentional overlap; source unchanged");
    cy.get('[data-testid="quality-review-slide-2"]')
      .should("contain.text", "Corrected detector false positive")
      .and("contain.text", "source unchanged");
    cy.get('[data-testid="quality-review-slide-0"]')
      .should("contain.text", "Apply: committed")
      .and("contain.text", "Rollback: no")
      .and("contain.text", "Changed properties: font-size, line-height");
    cy.get('[data-testid="quality-corrected-false-positive-count"]')
      .should("contain.text", "18");
    cy.get('[data-testid="quality-true-positive-verified-count"]')
      .should("contain.text", "0");
    cy.get('[data-testid="quality-true-positive-pending-count"]')
      .should("contain.text", "5");
    cy.get('[data-testid="quality-operations-dashboard"]')
      .should("contain.text", "Cold p99")
      .and("contain.text", "220ms")
      .and("contain.text", "Warm p50")
      .and("contain.text", "LLM / Chrome / acceptance / post-verify usage")
      .and("contain.text", "150")
      .and("contain.text", "$0.0123");
  });

  it("keeps a true-positive repair unresolved until canonical executor evidence exists", () => {
    const result = normalizeAuthoredQualityReviewResult({
      slide_results: [{
        slide_index: 18,
        attempted: false,
        accepted: false,
        applied: false,
        corrected_false_positive: false,
        apply_status: "not_started",
        publish_status: "not_started",
        post_verify_status: "not_started",
        rolled_back: false,
        detected_issues: ["element extends outside the slide canvas"],
        remaining_issues: ["footer glyph clipping"],
        path: "none",
      }],
    });

    expect(result.slide_results[0]).to.include({
      corrected_false_positive: false,
      applied: false,
      post_verify_status: "not_started",
    });
    expect(result.slide_results[0].remaining_issues).to.deep.equal([
      "footer glyph clipping",
    ]);
  });

  it("separates confirmed overlap, incomplete footer telemetry, and dependency-cone publish blocking", () => {
    cy.clock();
    cy.stub(PresentationGenerationApi, "reviewAuthoredPresentation")
      .resolves({ id: "quality-scoped-evidence-task" });
    cy.stub(PresentationGenerationApi, "getGenerationStatus").resolves({
      status: "completed",
      data: {
        reviewed_count: 20,
        issue_slide_count: 1,
        fixed_count: 0,
        remaining_issue_count: 1,
        remaining_slide_indices: [4],
        confirmed_issue_count: 1,
        incomplete_measurement_count: 1,
        unresolved_confirmed_issue_count: 1,
        clean_slide_count: 19,
        slide_results: [{
          slide_index: 4,
          attempted: false,
          accepted: false,
          applied: false,
          path: "none",
          detected_issues: ["bottom implication band occludes content"],
          remaining_issues: ["bottom implication band occludes content"],
          confirmed_issue_count: 1,
          unresolved_confirmed_issue_count: 1,
          evidence_atoms: [
            { qid: "q-div-63a4d3c2e6c7", metric: "occluded_fraction", stage: "chrome_occlusion", state: "proven_issue" },
            { qid: "q-div-655134fc7990", metric: "occluded_fraction", stage: "chrome_occlusion", state: "proven_issue" },
            { qid: "q-strong-f213b54d7db8", metric: "occluded_fraction", stage: "chrome_occlusion", state: "proven_issue" },
          ],
          incomplete_measurements: [{
            qid: "q-div-c6f2820b2605",
            metric: "occluded_fraction",
            stage: "chrome_occlusion",
            reason: "no_sample_on_owned_paint",
          }],
          publish_blockers: [{
            qid: "q-div-655134fc7990",
            constraint_id: "constraint:implication-band",
            metric: "occluded_fraction",
            stage: "publish_safety",
            reason: "target_dependency_unknown",
          }],
          projection_rejections: [{
            issue_code: "dom.unintended_overlap",
            element: { qid: "q-div-655134fc7990" },
            constraint_id: "dom:physical-overlap",
            operation: "ancestor_promotion",
            stage: "candidate_generation",
            metric: "owned_content_occluded_fraction",
            reason_code: "repair.geometry.ancestor.physical_owner_no_safe_translation",
            detector_evidence: {
              physicalOwnerBlockers: [{
                ownerQid: "q-div-f17e611a8d0f",
                requiredDownPx: 73.65625,
                availableDownPx: 37.359375,
                lowerBoundaryQid: "q-div-c6f2820b2605",
                lowerBoundaryYPx: 690,
                candidateContained: false,
              }],
            },
          }],
        }],
      },
    });

    cy.mount(<AuthoredQualityReview presentationId="deck-scoped-evidence" slideCount={20} currentSlide={4} />);
    cy.get('[data-testid="quality-review-trigger"]').click();
    cy.get('[data-testid="quality-review-confirm"]').click();
    cy.tick(2_000);

    cy.get('[data-testid="quality-review-summary"]')
      .should("contain.text", "후속 조치 필요")
      .and("contain.text", "문제 슬라이드 1장")
      .and("contain.text", "상세 이슈 1개")
      .and("contain.text", "수정 슬라이드 0장")
      .and("contain.text", "잔여 이슈 1개")
      .and("not.contain.text", "검수 완료 · clean");
    cy.get('[data-testid="quality-lifecycle-detected"]').should("contain.text", "Slide 5");
    cy.get('[data-testid="quality-lifecycle-incomplete"]')
      .should("contain.text", "q-div-c6f2820b2605")
      .and("contain.text", "no_sample_on_owned_paint");
    cy.get('[data-testid="quality-lifecycle-remaining"]')
      .should("contain.text", "bottom implication band occludes content");
    cy.get('[data-testid="quality-confirmed-issues-4"]')
      .should("contain.text", "Confirmed physical issues: 1");
    cy.get('[data-testid="quality-incomplete-4"]')
      .should("contain.text", "q-div-c6f2820b2605")
      .and("contain.text", "chrome_occlusion")
      .and("contain.text", "no_sample_on_owned_paint");
    cy.get('[data-testid="quality-publish-blocker-4"]')
      .should("contain.text", "constraint:implication-band")
      .and("contain.text", "target_dependency_unknown");
    cy.get('[data-testid="quality-repair-blocker-4"]')
      .should("contain.text", "repair.geometry.ancestor.physical_owner_no_safe_translation")
      .and("contain.text", "owner=q-div-f17e611a8d0f")
      .and("contain.text", "requiredDownPx=73.65625")
      .and("contain.text", "availableDownPx=37.359375")
      .and("contain.text", "lowerBoundary=q-div-c6f2820b2605")
      .and("contain.text", "candidateContained=false");
  });

  it("normalizes maximum-repair metrics and keeps LLM work as a typed repair-plan path", () => {
    const result = normalizeAuthoredQualityReviewResult({
      metrics: {
        pattern_cluster_count: 4,
        pattern_representatives: 7,
        pattern_recipe_count: 3,
        pattern_reuse_attempts: 18,
        pattern_reuse_hits: 14,
        pattern_reuse_applied: 11,
        composite_plan_count: 5,
        composite_operations_merged: 9,
        early_render_rejections: 6,
        full_renders: 8,
        llm_pattern_calls: 2,
        individual_fallbacks: 3,
        llm_eligible_count: 20,
        llm_circuit_open_count: 4,
        no_candidate_reasons: ["llm_provider_error", "negative_cache_hit"],
        wall_time_ms: 484_000,
        stage_timings: { prescan: 21, publish: 44 },
        llm: {
          calls: 18,
          responses: 8,
          valid_plans: 0,
          provider_failures: 5,
          schema_rejected: 3,
          preflight_unprojectable: 9,
          rendered: 0,
        },
      },
      slide_results: [{
        slide_index: 1,
        attempted: true,
        accepted: true,
        applied: true,
        path: "llm_repair_plan",
        resolved_issues: ["caption overflow"],
        remaining_issues: [],
      }],
    });

    expect(result.metrics).to.include({
      pattern_cluster_count: 4,
      pattern_representative_count: 7,
      pattern_recipe_count: 3,
      pattern_reuse_attempts: 18,
      pattern_reuse_hits: 14,
      pattern_reuse_applied: 11,
      composite_plan_count: 5,
      composite_operations_merged: 9,
      render_candidates_rejected_early: 6,
      full_render_count: 8,
      llm_pattern_call_count: 2,
      individual_fallback_count: 3,
      wall_time_ms: 484_000,
      aggregate_stage_time_ms: 65,
      llm_calls: 18,
      llm_eligible: 20,
      llm_responses: 8,
      llm_valid_plans: 0,
      llm_provider_failures: 5,
      llm_schema_rejected: 3,
      proposal_preflight_unprojectable: 9,
      llm_rendered: 0,
      llm_circuit_open: 4,
    });
    expect(result.metrics.llm_no_candidate_reasons).to.deep.equal([
      "llm_provider_error",
      "negative_cache_hit",
    ]);
    expect(result.metrics.stage_timings_ms).to.deep.equal({ prescan: 21, publish: 44 });
    expect(result.slide_results[0]?.path).to.equal("llm_repair_plan");
  });

  it("keeps slide-index-free issue details distinct from incomplete measurement", () => {
    const result = normalizeAuthoredQualityReviewResult({
      remaining_issue_count: 1,
      incomplete_measurement_count: 0,
      remaining_issues: [{ code: "dom.clipping", detail: "source summary omitted the slide index" }],
    });

    expect(result.unattributed_issues).to.deep.equal(["source summary omitted the slide index"]);
    expect(result.incomplete_measurement_count).to.equal(0);
  });
});
