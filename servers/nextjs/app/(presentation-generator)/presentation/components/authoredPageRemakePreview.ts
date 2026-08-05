/**
 * Reads the AI-authored one-page remake preview payload and turns it into
 * user-facing Korean text.
 *
 * The backend reports two things that are not presentable as-is: DOM quality
 * findings (English category/severity/detail records) and failure codes
 * (internal snake_case identifiers). Both are translated here so the flow
 * component only renders sentences a normal user can act on.
 */

export type PageRemakeIssue = {
  category?: string;
  severity?: string;
  detail?: string;
  qids?: string[];
  policy?: string;
};

export type PageRemakePreview = {
  kind: "authored_page_remake_preview";
  phase: "preview_ready" | "published";
  slide_index: number;
  candidate_image: string;
  candidate_digest: string;
  source_digest: string;
  baseline_issues?: PageRemakeIssue[];
  candidate_issues?: PageRemakeIssue[];
  validation?: {
    source_render?: string;
    candidate_render?: string;
    candidate_dom?: string;
    candidate_dom_telemetry?: string;
    candidate_change?: string;
    reference_mode?: string;
  };
  generation?: { elapsed_ms?: number; llm_scope?: string };
};

/**
 * A flagged candidate is still a usable page: the backend only asks for a human
 * look, so it must never be presented as a generation failure.
 */
export function hasQualityWarning(preview: PageRemakePreview) {
  return preview.validation?.candidate_dom === "warning";
}

/**
 * An identical re-author is an ordinary outcome once a page has already been
 * remade. It is a result to report, not an error to hide.
 */
export function isUnchangedCandidate(preview: PageRemakePreview) {
  return preview.validation?.candidate_change === "unchanged";
}

export const PAGE_REMAKE_UNCHANGED_HEADLINE =
  "새 페이지가 기존 페이지와 동일합니다";
export const PAGE_REMAKE_UNCHANGED_BODY =
  "다시 만든 결과가 현재 페이지와 같습니다. 이미 정리된 페이지일 수 있습니다. 그대로 두거나 다시 만들어 보세요.";

export type PageRemakeIssueGroup = {
  category: string;
  label: string;
  count: number;
  severity?: string;
  severityLabel?: string;
};

export type PageRemakeFailure = {
  message: string;
  code?: string;
};

const ISSUE_CATEGORY_LABELS: Record<string, string> = {
  overlap: "겹침",
  clipping: "잘림",
  off_canvas: "화면 벗어남",
  overflow: "넘침",
  font: "글꼴·가독성",
  image: "이미지",
  content: "내용",
  contrast: "명암 대비",
};

const OTHER_CATEGORY = "other";
const OTHER_CATEGORY_LABEL = "기타 확인 항목";

const SEVERITY_LABELS: Record<string, string> = {
  high: "중요도 높음",
  medium: "중요도 보통",
  low: "중요도 낮음",
};

const SEVERITY_RANK: Record<string, number> = { high: 3, medium: 2, low: 1 };

function severityRank(severity?: string) {
  return (severity && SEVERITY_RANK[severity]) || 0;
}

/**
 * Collapses the raw issue rows into one line per category so the panel stays
 * readable even when the inspector reports dozens of findings.
 */
export function summarizePageRemakeIssues(
  issues?: PageRemakeIssue[],
): PageRemakeIssueGroup[] {
  const groups = new Map<string, { count: number; severity?: string }>();

  for (const issue of issues ?? []) {
    const category =
      issue?.category && ISSUE_CATEGORY_LABELS[issue.category]
        ? issue.category
        : OTHER_CATEGORY;
    const current = groups.get(category) ?? { count: 0 };
    groups.set(category, {
      count: current.count + 1,
      severity:
        severityRank(issue?.severity) > severityRank(current.severity)
          ? issue?.severity
          : current.severity,
    });
  }

  return Array.from(groups.entries())
    .map(([category, group]) => ({
      category,
      label: ISSUE_CATEGORY_LABELS[category] ?? OTHER_CATEGORY_LABEL,
      count: group.count,
      severity: group.severity,
      severityLabel: group.severity
        ? SEVERITY_LABELS[group.severity]
        : undefined,
    }))
    .sort(
      (a, b) =>
        severityRank(b.severity) - severityRank(a.severity) ||
        b.count - a.count ||
        a.label.localeCompare(b.label),
    );
}

/** Shown when the candidate is flagged but no readable issue row came back. */
export function pageRemakeTelemetryNote(telemetry?: string) {
  return telemetry === "unavailable" || telemetry === "incomplete"
    ? "자동 검사를 끝까지 수행하지 못했습니다. 미리보기를 직접 확인해 주세요."
    : "확인이 필요한 항목의 종류를 특정하지 못했습니다. 미리보기를 직접 확인해 주세요.";
}

export const PAGE_REMAKE_PREVIEW_FAILURE_FALLBACK =
  "페이지를 다시 만들지 못했습니다. 잠시 후 다시 시도해 주세요.";

export const PAGE_REMAKE_PUBLISH_FAILURE_FALLBACK =
  "새 버전을 적용하지 못했습니다. 잠시 후 다시 시도해 주세요.";

const FAILURE_MESSAGES: Record<string, string> = {
  // The legacy write route is fenced off for chat-first decks, which must
  // publish through the workspace command pipeline instead.
  chat_first_command_required:
    "이 발표자료는 작업 대화를 통해서만 적용할 수 있습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
  authored_page_remake_generation_failed:
    "새 페이지를 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
  authored_page_remake_candidate_render_failed:
    "새 페이지를 이미지로 그리지 못했습니다. 잠시 후 다시 시도해 주세요.",
  authored_page_remake_candidate_invalid:
    "새 페이지 결과가 올바르지 않아 사용할 수 없습니다. 다시 만들어 주세요.",
  authored_page_remake_source_render_failed:
    "현재 페이지를 불러오지 못해 새 버전을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
  authored_page_remake_source_changed:
    "작업 중에 원본 페이지가 변경되었습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
  authored_page_remake_publish_cas_conflict:
    "다른 변경이 먼저 반영되어 적용하지 못했습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
  authored_page_remake_exact_source_unavailable:
    "이 페이지의 원본 데이터를 찾을 수 없어 다시 만들 수 없습니다.",
  authored_page_remake_slide_out_of_range:
    "선택한 페이지 번호가 올바르지 않습니다. 페이지를 다시 선택해 주세요.",
  authored_page_remake_preview_incomplete:
    "미리보기 결과가 완전하지 않습니다. 다시 만들어 주세요.",
  authored_page_remake_preview_target_mismatch:
    "미리보기 결과가 현재 페이지와 일치하지 않습니다. 다시 만들어 주세요.",
  authored_page_remake_post_verify_failed:
    "적용 후 검증에 실패해 변경을 되돌렸습니다. 잠시 후 다시 시도해 주세요.",
  authored_page_remake_failed:
    "페이지를 다시 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
};

/** Internal identifiers such as `authored_page_remake_failed`. */
const INTERNAL_CODE_PATTERN = /^[a-z][a-z0-9]*(_[a-z0-9]+)+$/;

function rawFailureText(error: unknown) {
  const raw =
    typeof error === "string"
      ? error
      : error instanceof Error
        ? error.message
        : "";
  return raw.trim() || undefined;
}

/**
 * Turns a task error detail (or a thrown error) into a Korean sentence, keeping
 * the internal code only as secondary diagnostic text.
 */
export function describePageRemakeFailure(
  error: unknown,
  fallback: string,
): PageRemakeFailure {
  const raw = rawFailureText(error);
  if (!raw) return { message: fallback };
  const mapped = FAILURE_MESSAGES[raw];
  if (mapped) return { message: mapped, code: raw };
  if (INTERNAL_CODE_PATTERN.test(raw)) return { message: fallback, code: raw };
  return { message: raw };
}
