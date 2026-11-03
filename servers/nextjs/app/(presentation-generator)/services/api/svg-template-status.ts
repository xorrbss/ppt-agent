import type { SvgTemplateDraft, SvgTemplateFrame, SvgTemplateIssue } from "./svg-templates";

export function templateReason(value: string | null | undefined): string {
  const code = value ?? "";
  if (code === "svg_template_powerpoint_busy") return "서버의 PowerPoint가 사용 중입니다. 잠시 후 분석을 다시 확인해 주세요.";
  // Review-required states before approval: the person's next step, not a failure.
  if (code === "source_render_review_required") return "원본과 보존할 양식의 미리보기를 비교해 확인해 주세요.";
  if (code === "classification_review_required") return "보존 요소와 본문 영역의 구분이 맞는지 확인해 주세요.";
  if (code === "svg_template_raster_preservation_review_required") return "지원하지 않는 장식 일부를 원본에서 잘라낸 이미지로 보존했습니다. 이 장식은 PPTX에서 그림으로 내보내져 편집할 수 없으니, 미리보기에서 위치와 모양을 확인해 주세요.";
  if (code === "svg_template_raster_renderer_unavailable") return "지원하지 않는 장식을 이미지로 보존할 LibreOffice를 사용할 수 없습니다. 서버 준비 상태를 확인해 주세요.";
  if (code.startsWith("svg_template_raster_")) return "장식을 이미지로 보존하는 렌더링을 완료하지 못했습니다. 분석을 다시 실행해 주세요.";
  if (code === "svg_template_fullslide_raster_forbidden") return "슬라이드 전체를 덮는 그림은 다른 양식 요소보다 아래에 있을 때만 배경으로 보존할 수 있습니다.";
  if (code === "svg_template_font_substitution_invalid") return "글꼴 대체 설정이 올바르지 않습니다. 원본 글꼴마다 대체 글꼴을 하나씩 지정해 주세요.";
  if (code === "svg_template_source_pages_invalid") return "분석할 원본 장 번호가 올바르지 않습니다.";
  if (code.includes("libreoffice")) return "원본 미리보기를 만들 LibreOffice를 사용할 수 없습니다. 서버 준비 상태를 확인해 주세요.";
  if (code.includes("embedding")) return "이 글꼴은 파일에 포함할 수 없어 원본 글꼴 보존을 확인할 수 없습니다.";
  if (code.includes("font") && code.includes("unsupported")) return "원본 글꼴의 서식이 현재 지원 범위에 포함되지 않습니다.";
  if (code.includes("font")) return "원본 글꼴을 준비하거나 정확히 표시할 수 없습니다. 글꼴 준비 상태를 확인해 주세요.";
  if (code.includes("powerpoint")) return "원본을 비교할 PowerPoint 렌더링을 사용할 수 없습니다. 서버 준비 상태를 확인해 주세요.";
  if (code.includes("preparation_required")) return "선택한 크기의 양식 준비가 필요합니다.";
  if (code.includes("disabled")) return "새 사용자 템플릿 생성은 현재 비활성화되어 있습니다. 기존 문서의 편집과 내보내기는 계속 이용할 수 있습니다.";
  if (code.includes("master") || code.includes("common_title") || code.includes("common_header")) return "양식과 중복되는 회사 양식 또는 공통 제목 설정을 해제해 주세요.";
  if (code.includes("source_mode") || code.includes("source_content") || code.includes("raster")) return "선택한 원본 보존 방식에서는 이 템플릿을 사용할 수 없습니다.";
  if (code.includes("fit") || code.includes("bounds") || code.includes("region") || code.includes("overlap") || code.includes("canvas")) return "내용이나 보존 요소가 사용 영역에 맞지 않습니다. 영역과 출력 크기를 확인해 주세요.";
  if (code.includes("unsupported")) return "보존 요소에 현재 지원하지 않는 표현이 있습니다. 요소를 수정하거나 디자인 참조로만 등록해 주세요.";
  if (code.includes("render") || code.includes("image") || code.includes("review")) return "원본과 양식의 비교를 완료하지 못했습니다. 미리보기를 다시 확인해 주세요.";
  return "템플릿 검증을 완료하지 못했습니다. 준비 상태를 확인한 뒤 다시 시도해 주세요.";
}
export const templateIssueText = (issue: string | SvgTemplateIssue) => templateReason(typeof issue === "string" ? issue : issue.reason_code ?? issue.code);
export const templateFontText = (state: string) => state === "ready" ? "준비 완료" : state === "legacy-profile" ? "기본 제공 글꼴" : "준비 필요";
export const templateRoleText = (role?: string) => role === "cover" ? "표지" : role === "section" ? "구분 장표" : role === "body" ? "본문" : "양식";
const operationLabels: Record<string, string> = { queued: "대기 중", pending: "대기 중", running: "준비 중", succeeded: "완료", completed: "완료", failed: "준비 실패", cancelled: "취소됨", unknown: "상태 확인 필요" };
export const templateOperationText = (state: string) => operationLabels[state] ?? "상태 확인 필요";
export const templateActionText = (action: string | null) => action === "prepare-canvas" ? "크기 준비를 실행한 뒤 다시 확인해 주세요." : action === "review-template" ? "템플릿 분석 결과와 설정을 확인해 주세요." : "";
export function frameReviewBlockReason(draft: SvgTemplateDraft, frame: SvgTemplateFrame): string | null {
  const evidence = draft.reviewEvidence ?? draft.review_evidence;
  if (evidence?.status === "blocked") return templateReason(evidence.reason);
  const issue = [...(draft.package?.issues ?? []), ...(frame.issues ?? [])].find(item => typeof item !== "string" && item.severity === "blocked");
  if (issue) return templateIssueText(issue);
  const record = evidence?.frames?.find(item => item.frame_id === frame.frame_id);
  if (record?.status === "blocked") return templateReason(record.reason);
  // Successful backend entries omit status; approval is still the user's next step.
  if (!record || !record.source_image_digest || !record.frame_image_digest) return "원본과 양식을 비교할 검증 자료가 준비되지 않았습니다.";
  if (!draft.source_previews?.[String(frame.source_page)] || !draft.source_previews?.[`frame:${frame.frame_id}`]) return "원본과 양식의 미리보기가 모두 준비되어야 선택할 수 있습니다.";
  return null;
}

const rasterReasonLabels: Array<[string, string]> = [
  ["background", "배경"], ["image_crop", "그림 자르기"], ["image_effect", "그림 효과"], ["image_", "그림 형식"],
  ["paint", "채우기"], ["effect", "효과"], ["primitive", "도형 모양"], ["geometry", "도형 모양"],
  ["transform", "회전·대칭"], ["line_style", "선 스타일"], ["color", "색"], ["style", "테마 스타일"],
];
const rasterReasonText = (code: string) => rasterReasonLabels.find(([key]) => code.includes(key))?.[1] ?? "지원하지 않는 표현";
/** One line per decoration image of a frame: what was kept as a picture and why. */
export function rasterPreservationLines(frame: SvgTemplateFrame): string[] {
  return (frame.elements ?? []).flatMap(element => {
    const raster = element.raster_preservation;
    if (!raster) return [];
    const renderer = raster.renderer === "libreoffice" ? "LibreOffice" : raster.renderer;
    const reasons = [...new Set(raster.reasons.map(rasterReasonText))].join(", ");
    return [`이미지로 보존(${element.layer === "background" ? "배경" : "장식"}) · 원본 요소 ${raster.shape_refs.length}개 · ${reasons} · ${renderer}${raster.approximate ? " 근사 렌더링" : ""} · PPTX에서 편집할 수 없음`];
  });
}
/** Source shape references a frame element stands for; the slide background is not selectable. */
export function frameElementShapeRefs(element: NonNullable<SvgTemplateFrame["elements"]>[number]): string[] {
  if (element.raster_preservation) return element.raster_preservation.shape_refs.filter(ref => !/#(implicit-)?background$/.test(ref));
  if (element.source_shape_id === "background" || element.source_shape_id === "implicit-background") return [];
  return [`${element.source_part}#${element.source_shape_id}`];
}
