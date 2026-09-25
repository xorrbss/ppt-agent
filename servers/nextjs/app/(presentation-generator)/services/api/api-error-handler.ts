// API Error Response Interface
interface StructuredApiError {
  status_code?: number;
  detail?: unknown;
  code?: string;
  message?: string;
  filename?: string;
  request_id?: string | null;
  stage?: string;
  slide_number?: number;
  retryable?: boolean;
  exception_type?: string;
  fingerprint?: string;
}

interface ApiErrorResponse {
  detail?: unknown;
  message?: string;
  error?: string | StructuredApiError;
  request_id?: string | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly filename?: string;
  readonly requestId?: string;
  readonly detail?: unknown;
  readonly stage?: string;
  readonly slideNumber?: number;
  readonly retryable?: boolean;
  readonly exceptionType?: string;
  readonly fingerprint?: string;
  readonly workspaceProjected: boolean;

  constructor(
    message: string,
    options: {
      status?: number;
      code?: string;
      filename?: string;
      requestId?: string | null;
      detail?: unknown;
      stage?: string;
      slideNumber?: number;
      retryable?: boolean;
      exceptionType?: string;
      fingerprint?: string;
      workspaceProjected?: boolean;
    } = {}
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? 0;
    this.code = options.code;
    this.filename = options.filename;
    this.requestId = options.requestId || undefined;
    this.detail = options.detail;
    this.stage = options.stage;
    this.slideNumber = options.slideNumber;
    this.retryable = options.retryable;
    this.exceptionType = options.exceptionType;
    this.fingerprint = options.fingerprint;
    this.workspaceProjected = options.workspaceProjected === true;
  }
}

export const AUTH_SESSION_EXPIRED_EVENT = "presenton:auth-session-expired";

export const isUnauthorizedApiError = (error: unknown): error is ApiError =>
  error instanceof ApiError && error.status === 401;

const signalExpiredAuthSession = (status: number) => {
  if (status !== 401 || typeof window === "undefined") return;
  window.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT));
};

// Non-retryable 409s from re-POSTing a durable generation with a stored
// idempotency key. The client clears the stored task on these errors, so the
// next generate click starts a fresh job with a new key.
const IDEMPOTENCY_ERROR_MESSAGES: Record<string, string> = {
  idempotency_key_payload_conflict:
    "이전에 중단된 생성과 요청 내용이 달라 이어서 생성할 수 없습니다. 생성을 다시 실행하면 새 작업으로 처음부터 생성됩니다.",
  idempotency_record_incomplete:
    "이전 생성 기록이 불완전하여 이어서 생성할 수 없습니다. 생성을 다시 실행하면 새 작업으로 처음부터 생성됩니다.",
  resume_attempts_exhausted:
    "재개 시도 횟수를 초과하여 이어서 생성할 수 없습니다. 생성을 다시 실행하면 새 작업으로 처음부터 생성됩니다.",
};

const SOURCE_SVG_ERROR_MESSAGES: Record<string, string> = {
  svg_source_chart_unsupported:
    "차트가 포함된 페이지는 현재 원본 보존 SVG로 재배치할 수 없습니다. 차트를 이미지로 바꾼 PPTX를 사용해 주세요.",
  svg_source_image_appearance_unsupported:
    "일부 이미지의 자르기·회전 또는 표시 효과를 그대로 보존할 수 없습니다. 해당 이미지를 일반 그림으로 바꾼 뒤 다시 시도해 주세요.",
  svg_source_picture_bullet_unsupported:
    "그림 글머리표는 현재 원본 보존 SVG에서 지원하지 않습니다. 일반 글머리표로 바꾼 뒤 다시 시도해 주세요.",
  svg_source_image_only_page_not_supported:
    "이미지만 있는 페이지는 현재 원본 보존 SVG로 재배치할 수 없습니다. 페이지에 편집 가능한 텍스트를 추가하거나 일반 SVG 생성을 사용해 주세요.",
  svg_source_template_selection_required:
    "적용할 템플릿을 다시 선택해 주세요. 원본 보존 SVG는 선택한 템플릿 ID를 그대로 사용합니다.",
  svg_source_template_unsupported:
    "선택한 템플릿은 현재 원본 크기 SVG 재구성을 지원하지 않습니다. 지원되는 템플릿을 선택해 주세요.",
  svg_source_template_unavailable:
    "선택한 템플릿 자산을 확인할 수 없습니다. 템플릿을 다시 선택해 주세요.",
  svg_source_canvas_not_supported:
    "현재는 A4 가로 PPTX의 원본 물리 크기만 그대로 유지해 SVG로 재구성할 수 있습니다.",
  svg_source_text_box_unfit:
    "원문을 같은 페이지와 선택한 템플릿 영역 안에 모두 배치할 수 없습니다. 원문을 줄이지 않고 생성이 중단되었습니다.",
  svg_source_content_boxes_overlap:
    "원본 콘텐츠를 선택한 템플릿의 간격 규칙 안에서 겹치지 않게 배치할 수 없습니다.",
  svg_source_plan_input_mismatch:
    "원본 페이지와 배치 계획의 콘텐츠 연결이 일치하지 않아 생성이 중단되었습니다.",
  svg_source_plan_coverage_mismatch:
    "원본 콘텐츠 일부가 배치 계획에 정확히 연결되지 않아 생성이 중단되었습니다.",
  svg_source_plan_canvas_mismatch:
    "배치 계획의 캔버스가 분석된 원본 물리 크기와 일치하지 않습니다.",
};

const GENERATION_STAGE_LABELS: Record<string, string> = {
  author: "작성",
  illustrate: "이미지 생성",
  render: "렌더링",
  persist: "저장",
  finalize: "최종 파일 생성",
  completed: "완료",
};

function getGenerationFailureContext(error: ApiError): string | undefined {
  const parts: string[] = [];
  if (
    Number.isInteger(error.slideNumber) &&
    (error.slideNumber as number) > 0
  ) {
    parts.push(`${error.slideNumber}번 슬라이드`);
  }
  const stageLabel = error.stage
    ? GENERATION_STAGE_LABELS[error.stage]
    : undefined;
  if (stageLabel) parts.push(`${stageLabel} 단계`);
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

export function apiErrorFromPayload(
  payload: unknown,
  fallbackMessage: string,
  fallbackStatus = 500,
  fallbackRequestId?: string | null
): ApiError {
  const error =
    payload && typeof payload === "object"
      ? (payload as StructuredApiError)
      : undefined;
  const detail = error?.detail;
  const detailMessage =
    typeof detail === "string" && detail.length > 0 ? detail : undefined;
  return new ApiError(error?.message || detailMessage || fallbackMessage, {
    status: error?.status_code ?? fallbackStatus,
    code: error?.code || detailMessage,
    filename: error?.filename,
    requestId: error?.request_id || fallbackRequestId,
    detail,
    stage: error?.stage,
    slideNumber: error?.slide_number,
    retryable: error?.retryable,
    exceptionType: error?.exception_type,
    fingerprint: error?.fingerprint,
  });
}

export function getApiErrorDisplayMessage(
  error: unknown,
  fallbackMessage: string
): string {
  const apiError =
    error instanceof ApiError
      ? error
      : new ApiError(
          error instanceof Error && error.message
            ? error.message
            : fallbackMessage
        );
  const message =
    (apiError.code &&
      (IDEMPOTENCY_ERROR_MESSAGES[apiError.code] ||
        SOURCE_SVG_ERROR_MESSAGES[apiError.code.split(":", 1)[0]])) ||
    apiError.message ||
    fallbackMessage;
  const failureContext = getGenerationFailureContext(apiError);
  const contextualMessage = failureContext
    ? `${failureContext}: ${message}`
    : message;
  return apiError.requestId
    ? `${contextualMessage} (Request ID: ${apiError.requestId})`
    : contextualMessage;
}

/**
 * These errors normally come from an upstream model provider. Retrying an
 * authored presentation with the same idempotency key resumes its persisted
 * slide checkpoints instead of starting a second billed generation.
 */
export function isRetryablePresentationGenerationError(error: unknown): boolean {
  const apiError = error instanceof ApiError ? error : undefined;
  if (isPresentationGenerationSlideLimitError(error)) return false;
  if (typeof apiError?.retryable === "boolean") return apiError.retryable;
  const status = apiError?.status;
  const code = apiError?.code?.toLowerCase() || "";
  const message = apiError?.message?.toLowerCase() || "";

  return (
    status === 429 ||
    status === 500 ||
    status === 502 ||
    status === 503 ||
    status === 504 ||
    code.includes("retryable") ||
    code.includes("overload") ||
    message.includes("overload") ||
    message.includes("temporarily unavailable")
  );
}

export function isPresentationGenerationSlideLimitError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  const code = error.code?.trim().toLowerCase();
  const message = error.message.trim().toLowerCase();
  return (
    code === "public_slide_limit_exceeded" ||
    message === "public_slide_limit_exceeded"
  );
}

/** A concise Korean message for the presentation-generation retry flow. */
export function getRetryablePresentationGenerationMessage(error: unknown): string {
  const apiError = error instanceof ApiError ? error : undefined;
  const requestId = apiError?.requestId;
  const message = "AI 생성 서비스가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요.";
  const failureContext = apiError
    ? getGenerationFailureContext(apiError)
    : undefined;
  const contextualMessage = failureContext
    ? `${message} (실패 위치: ${failureContext})`
    : message;
  return requestId
    ? `${contextualMessage} (요청 ID: ${requestId})`
    : contextualMessage;
}

// API Response Handler Utility
export class ApiResponseHandler {
  private static structuredErrorDetail(
    detail: unknown,
  ): StructuredApiError | undefined {
    if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
      return undefined;
    }
    const candidate = detail as StructuredApiError;
    return typeof candidate.message === "string" ||
      typeof candidate.code === "string"
        ? candidate
        : undefined;
  }

  private static readableStructuredDetail(
    error: StructuredApiError | undefined,
  ): string | null {
    if (!error?.message) return null;
    if (!error.filename || error.message.includes(error.filename)) {
      return error.message;
    }
    return `${error.filename}: ${error.message}`;
  }

  private static normalizeErrorDetail(detail: unknown): string | null {
    if (!detail) return null;

    if (typeof detail === "string") {
      return detail;
    }

    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object") {
            const maybeMsg = (item as { msg?: unknown }).msg;
            const maybeLoc = (item as { loc?: unknown }).loc;
            const locPath = Array.isArray(maybeLoc)
              ? maybeLoc
                  .filter((v) => typeof v === "string" || typeof v === "number")
                  .join(".")
              : "";
            if (typeof maybeMsg === "string") {
              return locPath ? `${locPath}: ${maybeMsg}` : maybeMsg;
            }
          }
          return null;
        })
        .filter((v): v is string => Boolean(v));

      return parts.length ? parts.join("; ") : JSON.stringify(detail);
    }

    if (typeof detail === "object") {
      return JSON.stringify(detail);
    }

    return String(detail);
  }

 
  /**
   * Parses a successful response body, or throws `ApiError`. The body shape is
   * whatever the endpoint returns, so callers name it via `T` (either
   * explicitly or through the awaited call's contextual type). The `any`
   * default preserves the previous signature for the many legacy call sites
   * that still consume the body untyped; pass `T` at new call sites.
   */
  static async handleResponse<T = any>(response: Response, defaultErrorMessage: string): Promise<T> {
    // Handle successful responses
    if (response.ok) {
      // Handle 204 No Content responses
      if (response.status === 204) {
        return true as T;
      }

      // Try to parse JSON response
      try {
        return (await response.json()) as T;
      } catch {
        // If JSON parsing fails but response is ok, return empty object
        return {} as T;
      }
    }

    // Let the application shell replace the stale authenticated page with the
    // login screen. Callers still receive ApiError so their pending work stops.
    signalExpiredAuthSession(response.status);

    // Handle error responses
    let errorMessage = defaultErrorMessage;
    const workspaceProjected =
      response.headers
        .get("x-chat-first-workspace-projected")
        ?.trim()
        .toLowerCase() === "true";
    
    try {
      const errorData: ApiErrorResponse = await response.json();
      const structuredError =
        errorData.error && typeof errorData.error === "object"
          ? errorData.error
          : undefined;
      const detailError = this.structuredErrorDetail(errorData.detail);
      const errorMetadata = structuredError ?? detailError;
      
      // Extract error message in order of preference
      const normalizedDetail =
        this.readableStructuredDetail(detailError) ??
        this.normalizeErrorDetail(errorData.detail);
      if (normalizedDetail) {
        errorMessage = normalizedDetail;
      } else if (errorData.message) {
        errorMessage = errorData.message;
      } else if (typeof errorData.error === "string") {
        errorMessage = errorData.error;
      } else if (structuredError?.message) {
        errorMessage = structuredError.message;
      }

      throw new ApiError(errorMessage, {
        status: errorMetadata?.status_code ?? response.status,
        code: errorMetadata?.code,
        filename: errorMetadata?.filename,
        requestId:
          errorMetadata?.request_id ||
          errorData.request_id ||
          response.headers.get("x-request-id") ||
          response.headers.get("x-correlation-id"),
        detail: errorMetadata?.detail ?? errorData.detail,
        stage: errorMetadata?.stage,
        slideNumber: errorMetadata?.slide_number,
        retryable: errorMetadata?.retryable,
        exceptionType: errorMetadata?.exception_type,
        fingerprint: errorMetadata?.fingerprint,
        workspaceProjected,
      });
    } catch (error) {
      if (error instanceof ApiError) throw error;
      // If JSON parsing fails, use status-based messages.
      errorMessage = this.getStatusBasedErrorMessage(
        response.status,
        defaultErrorMessage
      );
    }

    // Throw error with appropriate message
    throw new ApiError(errorMessage, {
      status: response.status,
      requestId:
        response.headers.get("x-request-id") ||
        response.headers.get("x-correlation-id"),
      workspaceProjected,
    });
  }


  static async handleResponseWithResult(response: Response, defaultErrorMessage: string): Promise<{success: boolean, message?: string}> {
    try {
      // Handle successful responses
      if (response.ok) {
        return { success: true };
      }

      signalExpiredAuthSession(response.status);

      // Handle error responses
      let errorMessage = defaultErrorMessage;
      
      try {
        const errorData: ApiErrorResponse = await response.json();
        const structuredError =
          errorData.error && typeof errorData.error === "object"
            ? errorData.error
            : undefined;
        const detailError = this.structuredErrorDetail(errorData.detail);
        
        // Extract error message in order of preference
        const normalizedDetail =
          this.readableStructuredDetail(detailError) ??
          this.normalizeErrorDetail(errorData.detail);
        if (normalizedDetail) {
          errorMessage = normalizedDetail;
        } else if (errorData.message) {
          errorMessage = errorData.message;
        } else if (typeof errorData.error === "string") {
          errorMessage = errorData.error;
        } else if (structuredError?.message) {
          errorMessage = structuredError.message;
        }
      } catch {
        // If JSON parsing fails, use status-based messages
        errorMessage = this.getStatusBasedErrorMessage(response.status, defaultErrorMessage);
      }

      return {
        success: false,
        message: errorMessage,
      };
    } catch (error) {
      return {
        success: false,
        message: error instanceof Error ? error.message : defaultErrorMessage,
      };
    }
  }


  private static getStatusBasedErrorMessage(status: number, defaultMessage: string): string {
    switch (status) {
      case 400:
        return "잘못된 요청입니다. 입력 내용을 확인한 뒤 다시 시도해 주세요.";
      case 401:
        return "인증이 필요합니다. 로그인 후 다시 시도해 주세요.";
      case 403:
        return "접근이 거부되었습니다. 이 작업을 수행할 권한이 없습니다.";
      case 404:
        return "리소스를 찾을 수 없습니다. 삭제되었거나 이동되었을 수 있습니다.";
      case 409:
        return "충돌이 발생했습니다. 리소스가 이미 존재하거나 현재 상태와 충돌합니다.";
      case 422:
        return "입력값 검증에 실패했습니다. 입력 내용을 확인한 뒤 다시 시도해 주세요.";
      case 429:
        return "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.";
      case 500:
        return "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
      case 502:
        return "게이트웨이 오류입니다. 서버를 일시적으로 사용할 수 없습니다.";
      case 503:
        return "서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.";
      case 504:
        return "게이트웨이 시간 초과입니다. 요청 처리가 너무 오래 걸렸습니다.";
      default:
        return defaultMessage;
    }
  }
}

export type { ApiErrorResponse, StructuredApiError };
