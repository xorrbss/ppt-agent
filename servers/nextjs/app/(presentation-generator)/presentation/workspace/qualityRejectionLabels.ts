/**
 * Plain-language readings for the repair pipeline's rejection codes.
 *
 * The review already records why each slide was left unfixed, but the raw code
 * ("deterministic_no_candidate") does not distinguish "no repair could even be
 * drafted" from "a repair was drafted and then failed" - the exact question a
 * reader asks when the card says only "수정 조치: none".
 */
const REASON_LABELS: Record<string, string> = {
  // No candidate was produced at all.
  deterministic_no_candidate: "복구안을 만들지 못했습니다",
  llm_empty: "복구 모델이 제안을 내지 않았습니다",
  llm_provider_error: "복구 모델을 호출하지 못했습니다",
  llm_schema_rejected: "복구 모델 응답이 규격에 맞지 않았습니다",
  negative_cache_hit: "직전 동일 요청이 실패해 재시도를 건너뛰었습니다",
  proposal_preflight_unprojectable: "제안을 원본 구조에 대응시키지 못했습니다",
  "repair.typed_pipeline_unavailable": "복구 파이프라인을 사용할 수 없었습니다",

  // A candidate existed but did not survive rendering or validation.
  "render.source_projection.roundtrip_failed":
    "복구안을 원본에 되돌려 적용하지 못했습니다",
  "vqa.improvement_unavailable": "개선 여부를 측정하지 못했습니다",
  "dom.clipping": "고친 뒤에도 잘림이 남았습니다",
  "dom.overflow": "고친 뒤에도 넘침이 남았습니다",
  "dom.off_canvas": "고친 뒤에도 화면 밖 요소가 남았습니다",
  "dom.unintended_overlap": "고친 뒤에도 겹침이 남았습니다",
  "constraint.readability.violated": "고친 뒤에도 가독성 기준에 못 미쳤습니다",
  "constraint.containment.violated": "고친 뒤에도 요소가 영역을 벗어났습니다",
  "constraint.non_overlap.violated": "고친 뒤에도 겹침 규칙을 위반했습니다",
  "constraint.gap.violated": "고친 뒤에도 간격 규칙을 위반했습니다",
  "constraint.alignment.violated": "고친 뒤에도 정렬 규칙을 위반했습니다",
  "constraint.order.violated": "고친 뒤에도 순서 규칙을 위반했습니다",
  "constraint.evidence.missing": "판정 근거가 부족했습니다",

  // The slide never reached repair.
  "dom.telemetry_incomplete": "화면 측정이 불완전해 판정하지 못했습니다",
  "legacy_preserved.no_geometry_trigger": "고칠 기하 결함이 없었습니다",
  "legacy_preserved.disabled": "자동 수정이 꺼져 있었습니다",
};

export function qualityRejectionLabel(reason: string): string | undefined {
  return REASON_LABELS[reason];
}
