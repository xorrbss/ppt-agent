import type { SvgTemplateDraft, SvgTemplateFrame, SvgTemplateIssue } from "./svg-templates";

export function templateReason(value: string | null | undefined): string {
  const code = value ?? "";
  if (code === "svg_template_powerpoint_busy") return "서버의 PowerPoint가 사용 중입니다. 잠시 후 분석을 다시 확인해 주세요.";
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
