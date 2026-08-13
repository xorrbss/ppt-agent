import React from "react";

import { WorkspaceOperationCard } from "./WorkspaceOperationCard";

describe("WorkspaceOperationCard quality review", () => {
  it("separates quality counts, surfaces all maximum-repair metrics, and focuses residual issues", () => {
    const onFocusSlide = cy.stub().as("focusSlide");
    cy.mount(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "quality-operation-anchor",
          operationId: "quality-operation",
          position: 0,
          operationType: "quality.review",
          title: "품질 검토",
          status: "succeeded",
          events: [],
          allowedActions: [],
          result: {
            kind: "quality.review",
            reviewed_count: 12,
            issue_slide_count: 4,
            confirmed_issue_count: 7,
            fixed_count: 3,
            fixed_slide_indices: [1, 3, 8],
            resolved_issue_count: 5,
            remaining_issue_count: 2,
            remaining_issues: [{
              code: "dom.clipping",
              reason: "deck-level summary did not include slide index",
            }],
            slide_results: [{
              slide_index: 6,
              remaining_issues: [{
                code: "text_overflow",
                reason: "constraint.no_safe_space",
              }],
            }],
            metrics: {
              pattern_cluster_count: 2,
              pattern_representative_count: 4,
              pattern_recipe_count: 2,
              pattern_reuse_attempts: 7,
              pattern_reuse_hits: 5,
              pattern_reuse_applied: 4,
              composite_plan_count: 3,
              composite_operations_merged: 6,
              render_candidates_rejected_early: 2,
              full_render_count: 5,
              llm_pattern_call_count: 1,
              llm_eligible_count: 18,
              llm_circuit_open_count: 5,
              no_candidate_reasons: ["llm_provider_error", "negative_cache_hit"],
              individual_fallback_count: 2,
              wall_time_ms: 484_000,
              llm: {
                calls: 18,
                valid_plans: 0,
                provider_failures: 5,
                schema_rejected: 3,
                preflight_unprojectable: 9,
                rendered: 0,
              },
              stage_timings_ms: { prescan: 14, publish: 25 },
            },
          },
        }}
        onAction={() => undefined}
        onFocusSlide={onFocusSlide}
      />,
    );

    cy.get('[data-testid="quality-review-result"]')
      .should("contain.text", "문제 슬라이드")
      .and("contain.text", "4장")
      .and("contain.text", "상세 이슈")
      .and("contain.text", "7개")
      .and("contain.text", "수정 슬라이드")
      .and("contain.text", "3장")
      .and("contain.text", "해결 이슈")
      .and("contain.text", "5개")
      .and("contain.text", "잔여 이슈")
      .and("contain.text", "2개");
    cy.get('[data-testid="quality-review-pipeline-metrics"]')
      .should("contain.text", "대표 표본 4")
      .and("contain.text", "복합 계획 3")
      .and("contain.text", "전체 렌더 5")
      .and("contain.text", "LLM 호출 18")
      .and("contain.text", "유효 RepairPlan 0")
      .and("contain.text", "LLM provider 실패 5")
      .and("contain.text", "LLM schema 거부 3")
      .and("contain.text", "투영 preflight 차단 9")
      .and("contain.text", "LLM 후보 렌더 0")
      .and("contain.text", "LLM RepairPlan 1")
      .and("contain.text", "prescan 14ms")
      .and("contain.text", "publish 25ms");
    cy.get('[data-testid="quality-review-pipeline-metrics"]')
      .should("contain.text", "LLM circuit open 5");
    cy.get('[data-testid="quality-review-llm-no-candidate-reasons"]')
      .should("contain.text", "llm_provider_error")
      .and("contain.text", "negative_cache_hit");
    cy.get('[data-testid="quality-review-wall-time"]').should("contain.text", "실제 경과 484초");
    cy.get('[data-testid="quality-review-stage-aggregate-time"]')
      .should("contain.text", "단계 누적 39ms")
      .and("contain.text", "병렬 합산");
    cy.get('[data-testid="quality-review-remaining-issues"]')
      .should("contain.text", "7번 슬라이드 · text_overflow")
      .and("contain.text", "사유: constraint.no_safe_space")
      .and("contain.text", "슬라이드 미지정 · dom.clipping");
    cy.get('[data-testid="quality-review-focus-remaining-slide-7"]').click();
    cy.get("@focusSlide").should("have.been.calledWith", 6);
  });
});
