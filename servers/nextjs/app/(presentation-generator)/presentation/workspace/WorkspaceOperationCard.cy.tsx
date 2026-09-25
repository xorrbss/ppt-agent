import React from "react";
import { NextIntlClientProvider } from "next-intl";
import koMessages from "@/i18n/messages/ko.json";

import { WorkspaceOperationCard } from "./WorkspaceOperationCard";
import { WorkspaceMessage } from "./WorkspaceMessage";
import type { WorkspaceOperationAnchor } from "./types";

const mountCard = (card: React.ReactElement) =>
  cy.mount(
    <NextIntlClientProvider locale="ko" messages={koMessages} timeZone="Asia/Seoul">
      {card}
    </NextIntlClientProvider>,
  );

describe("WorkspaceOperationCard quality review", () => {
  const generation: WorkspaceOperationAnchor = {
    anchorType: "operation", id: "generation-v3", operationId: "generation-v3",
    position: 0, operationType: "presentation.generate", title: "프레젠테이션 생성",
    status: "running", progress: 35, events: [], allowedActions: [],
  };

  it("shows one Korean failure without terminal animation, a main percentage or forbidden retry", () => {
    mountCard(<WorkspaceOperationCard operation={{ ...generation, status: "failed", error: "Generation failed", retryable: false,
      events: [{ id: "failure", operationId: generation.operationId, sequence: 2, type: "operation.failed", message: "Generation failed" }],
      allowedActions: [{ id: "retry", label: "다시 시도", enabled: false, variant: "primary" }],
    }} onAction={() => undefined} />);
    cy.get('[role="alert"]').should("have.length", 1).and("contain.text", "생성을 완료하지 못했어요");
    cy.contains("Generation failed").should("not.exist");
    cy.get('[role="progressbar"], .animate-pulse, [data-testid="operation-retry"]').should("not.exist");
    cy.get('[data-testid="operation-retry-unavailable"]').should("not.exist");
  });

  it("uses actual stage counts and keeps generation cancel in the composer while export retains cancel", () => {
    const cancel = { id: "cancel" as const, label: "취소", enabled: true, variant: "secondary" as const };
    mountCard(<WorkspaceOperationCard operation={{ ...generation, allowedActions: [cancel],
      events: [{ id: "progress", operationId: generation.operationId, sequence: 1, type: "operation.progress", stage: "authored_render", current: 3, total: 30 }],
    }} composerCancelOperationId={generation.operationId} onAction={() => undefined} />);
    cy.get('[data-testid="generation-progress-response"]').should("contain.text", "3/30장");
    cy.get('[data-testid="operation-cancel"], [role="progressbar"]').should("not.exist");
    mountCard(<WorkspaceOperationCard operation={{ ...generation, operationType: "export.pptx", allowedActions: [cancel] }} onAction={() => undefined} />);
    cy.get('[data-testid="operation-cancel"]').should("be.enabled");
  });

  it("does not treat a percent heartbeat as real progress after sixty seconds", () => {
    const now = Date.parse("2026-09-23T10:00:00Z");
    cy.clock(now);
    mountCard(<WorkspaceOperationCard operation={{ ...generation,
      events: [
        { id: "one", operationId: generation.operationId, sequence: 1, type: "operation.progress", stage: "authored_render", current: 3, total: 30, progress: 61, createdAt: new Date(now - 70_000).toISOString() },
        { id: "heartbeat", operationId: generation.operationId, sequence: 2, type: "operation.progress", stage: "authored_render", current: 3, total: 30, progress: 64, createdAt: new Date(now).toISOString() },
      ],
    }} observation={{ lastConfirmedAt: new Date(now).toISOString(), connection: "confirmed" }} onAction={() => undefined} />);
    cy.get('[data-testid="generation-observation"]').should("contain.text", "진행 변화는 아직 없습니다").and("contain.text", "마지막 서버 확인").and("contain.text", "마지막 진행 변화");
  });

  it("keeps attachment metadata compact and settings collapsed", () => {
    mountCard(<WorkspaceMessage message={{ anchorType: "message", id: "request", position: 0, role: "user", content: "",
      generationSettings: { language: "한국어", api_key: "do-not-show" },
      attachments: [{ id: "file", name: "발표.pdf", status: "ready", mimeType: "application/pdf", size: 1024, stateAtTime: "ready", currentState: "ready" }],
    }} />);
    cy.contains("첨부 파일로 시작한 요청입니다").should("be.visible");
    cy.contains("발표.pdf").should("be.visible");
    cy.contains("application/pdf").should("not.exist");
    cy.contains("do-not-show").should("not.exist");
    cy.get("details").should("not.have.attr", "open");
  });

  it("labels PPTX completion from editability evidence and lists image fallback pages", () => {
    const exportOperation = (result: Record<string, unknown>) => ({
      anchorType: "operation" as const,
      id: "export-editability",
      operationId: "export-editability",
      position: 0,
      operationType: "export.pptx" as const,
      title: "PPTX 내보내기",
      status: "succeeded" as const,
      events: [],
      allowedActions: [],
      result: {
        artifact_id: "artifact-editability",
        format: "pptx",
        ...result,
      },
    });

    mountCard(
      <WorkspaceOperationCard
        operation={exportOperation({
          public_export_worker_evidence: {
            editability: {
              verdict: "partially-editable",
              imageOnlyPages: [9],
              slides: [],
            },
            quality: {
              status: "partially-editable",
              imageFallbackSlides: 4,
              slideFallbacks: [
                { n: 45, code: "raster_fragment_missing" },
                { n: 65, code: "raster_fragment_digest_mismatch" },
                { n: 89, code: "raster_fragment_placement_mismatch" },
              ],
            },
            producerTiming: {
              slides: [
                { n: 45, recovery: { cleanupFailure: true } },
              ],
            },
          },
        })}
        onAction={() => undefined}
      />,
    );

    cy.get('[data-testid="export-editability-verdict"]')
      .should("contain.text", "내보내기 완료 · 일부 편집 가능")
      .and("not.contain.text", "완전 편집형");
    cy.get('[data-testid="export-image-fallback-pages"]')
      .should("contain.text", "이미지로 대체된 페이지")
      .should("contain.text", "9페이지")
      .and("contain.text", "45페이지")
      .and("contain.text", "65페이지")
      .and("contain.text", "89페이지");
    cy.get('[data-testid="export-image-fallback-reasons"]')
      .should("contain.text", "그림 조각이 없어")
      .and("contain.text", "식별값이 원본과 일치하지 않아")
      .and("contain.text", "위치 측정값이 일치하지 않아")
      .and("not.contain.text", "raster_fragment_");
    cy.get('[data-testid="export-cleanup-status"]')
      .should("contain.text", "내보내기 종료 정리")
      .and("contain.text", "45페이지")
      .and("contain.text", "결과 파일과 페이지 측정 판정은 유지됩니다");

    mountCard(
      <WorkspaceOperationCard
        operation={exportOperation({
          public_export_worker_evidence: {
            editability: {
              verdict: "fully-editable",
              imageOnlyPages: [],
              slides: [],
            },
            quality: {
              status: "fully-editable",
              imageFallbackSlides: 0,
              slideFallbacks: [],
            },
          },
        })}
        onAction={() => undefined}
      />,
    );

    cy.get('[data-testid="export-editability-verdict"]')
      .should("contain.text", "내보내기 완료 · 완전 편집형 PPTX");
    cy.get('[data-testid="export-image-fallback-pages"]').should("not.exist");
  });

  it("does not present a design application as complete without retained-layout fidelity evidence", () => {
    mountCard(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "design-apply-pending-evidence",
          operationId: "design-apply-pending-evidence",
          position: 0,
          operationType: "design.apply",
          title: "AI 저작 디자인 적용",
          status: "succeeded",
          events: [],
          allowedActions: [],
          result: {
            kind: "design.apply",
            message: "작업이 완료되었습니다.",
            slide_count: 5,
          },
        }}
        onAction={() => undefined}
      />,
    );

    cy.get('[role="status"]').should("contain.text", "시각 검증 증거 대기");
    cy.get('[data-testid="template-apply-result"]')
      .should("contain.text", "완료로 표시하지 않습니다")
      .and("contain.text", "Reference-layout digest")
      .and("contain.text", "미확인");
  });

  it("recognizes the completed snake_case design receipt when preview and native evidence agree", () => {
    const snapshot = {
      reference_layout_digest: "sha256:reference-layout",
      composition_digest: "sha256:composition",
      rule_digest: "sha256:rule",
      slide_count: 8,
    };
    mountCard(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "design-apply-verified",
          operationId: "design-apply-verified",
          position: 0,
          operationType: "design.apply",
          title: "AI 저작 디자인 적용",
          status: "succeeded",
          events: [],
          allowedActions: [],
          result: {
            kind: "design.apply",
            message: "AI 저작 레이아웃 검증이 완료되었습니다.",
            template_id: "artifact-template-ai/osb-proposal-1-29",
            template_digest: "sha256:template",
            reference_layout_digest: snapshot.reference_layout_digest,
            composition_digest: snapshot.composition_digest,
            rule_digest: snapshot.rule_digest,
            visual_fidelity: { status: "passed" },
            page_numbers: [1, 2, 3, 4, 5, 6, 7, 8],
            slide_count: 8,
            preview_snapshot: snapshot,
            native_pptx_snapshot: snapshot,
            readback_verified: true,
          },
        }}
        onAction={() => undefined}
      />,
    );

    cy.get('[role="status"]').should("contain.text", "완료");
    cy.get('[data-testid="template-apply-result"]')
      .should("contain.text", "AI 저작 레이아웃 검증이 완료되었습니다")
      .and("contain.text", "동일한 resolved snapshot 확인");
    cy.get('[data-testid="template-apply-evidence"]')
      .should("contain.text", "artifact-template-ai/osb-proposal-1-29")
      .and("contain.text", "1, 2, 3, 4, 5, 6, 7, 8 / 8장")
      .and("contain.text", "검증됨");
  });

  it("separates quality counts, surfaces all maximum-repair metrics, and focuses residual issues", () => {
    const onFocusSlide = cy.stub().as("focusSlide");
    mountCard(
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

  it("offers no batch remake when the review only reported deck-level issues", () => {
    mountCard(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "quality-deck-level-issues",
          operationId: "quality-deck-level-issues",
          position: 0,
          operationType: "quality.review",
          title: "품질 검토",
          status: "succeeded",
          events: [],
          allowedActions: [],
          result: {
            kind: "quality.review",
            reviewed_count: 4,
            remaining_issues: [{ code: "dom.clipping" }, { code: "contrast" }],
          },
        }}
        onAction={() => undefined}
        onRemakePage={cy.stub().as("remakePage")}
      />,
    );

    // Without a page to rebuild there is nothing a remake could target.
    cy.get('[data-testid="quality-review-remaining-issues"]')
      .find('input[type="checkbox"]')
      .should("not.exist");
    cy.get('[data-testid="quality-review-batch-remake"]').should("not.exist");
  });

  it("requests one remake per residual page, not one per residual issue", () => {
    const onRemakePage = cy.stub().as("remakePage");
    mountCard(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "quality-batch-remake-one-page",
          operationId: "quality-batch-remake-one-page",
          position: 0,
          operationType: "quality.review",
          title: "품질 검토",
          status: "succeeded",
          events: [],
          allowedActions: [],
          result: {
            kind: "quality.review",
            reviewed_count: 9,
            slide_results: [
              {
                slide_index: 6,
                remaining_issues: [
                  { code: "text_overflow" },
                  { code: "dom.clipping", reason: "constraint.no_safe_space" },
                ],
              },
            ],
          },
        }}
        onAction={() => undefined}
        onRemakePage={onRemakePage}
      />,
    );

    cy.get('[data-testid^="quality-review-select-slide-"]').should("have.length", 1);
    cy.get('[data-testid="quality-review-select-slide-7"]').check();
    cy.get('[data-testid="quality-review-batch-remake"]').click();
    cy.get("@remakePage").should("have.been.calledOnceWith", [6]);
  });

  it("sends every residual page once when the reviewer selects them all", () => {
    const onRemakePage = cy.stub().as("remakePage");
    mountCard(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "quality-batch-remake-select-all",
          operationId: "quality-batch-remake-select-all",
          position: 0,
          operationType: "quality.review",
          title: "품질 검토",
          status: "succeeded",
          events: [],
          allowedActions: [],
          result: {
            kind: "quality.review",
            reviewed_count: 9,
            remaining_issues: [{ code: "deck.summary" }],
            slide_results: [
              { slide_index: 8, remaining_issues: [{ code: "text_overflow" }] },
              { slide_index: 2, remaining_issues: [{ code: "contrast" }] },
              { slide_index: 8, remaining_issues: [{ code: "dom.clipping" }] },
            ],
          },
        }}
        onAction={() => undefined}
        onRemakePage={onRemakePage}
      />,
    );

    cy.get('[data-testid="quality-review-select-all-slides"]').check();
    cy.get('[data-testid="quality-review-batch-remake"]')
      .should("contain.text", "선택한 2개 페이지 다시 만들기")
      .click();
    cy.get("@remakePage").should("have.been.calledOnceWith", [2, 8]);
  });

  it("says why an unfixed slide was left alone, not just that nothing was applied", () => {
    mountCard(
      <WorkspaceOperationCard
        operation={{
          anchorType: "operation",
          id: "quality-rejection-reason",
          operationId: "quality-rejection-reason",
          position: 0,
          operationType: "quality.review",
          title: "품질 검토",
          status: "succeeded",
          events: [],
          allowedActions: [],
          result: {
            kind: "quality.review",
            reviewed_count: 2,
            issue_slide_count: 2,
            fixed_count: 0,
            issues: [
              { slide_index: 12, type: "dom.clipping", severity: "medium" },
              { slide_index: 64, type: "constraint.readability.violated", severity: "high" },
            ],
            slide_results: [
              {
                slide_index: 12,
                attempted: true,
                applied: false,
                fix_action: "none",
                rejection_reason: "deterministic_no_candidate",
              },
              {
                slide_index: 64,
                attempted: true,
                applied: false,
                fix_action: "deterministic",
                rejection_reason: "some.future.code",
              },
            ],
          },
        }}
        onAction={() => undefined}
      />,
    );

    // "수정 조치: none" alone cannot distinguish "no repair was ever drafted"
    // from "a repair was drafted and rejected"; the reason row must.
    cy.get('[data-testid="quality-rejection-reason"]')
      .first()
      .should("contain.text", "복구안을 만들지 못했습니다")
      // The raw code stays on screen for log and telemetry lookups.
      .and("contain.text", "deterministic_no_candidate");
    // A code with no reading yet still reaches the reader verbatim.
    cy.get('[data-testid="quality-rejection-reason"]')
      .last()
      .should("contain.text", "some.future.code");
  });
});
