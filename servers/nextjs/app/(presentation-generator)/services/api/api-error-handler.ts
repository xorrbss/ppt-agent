// API Error Response Interface
interface StructuredApiError {
  status_code?: number;
  detail?: unknown;
  code?: string;
  message?: string;
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
  readonly requestId?: string;
  readonly detail?: unknown;
  readonly stage?: string;
  readonly slideNumber?: number;
  readonly retryable?: boolean;
  readonly exceptionType?: string;
  readonly fingerprint?: string;

  constructor(
    message: string,
    options: {
      status?: number;
      code?: string;
      requestId?: string | null;
      detail?: unknown;
      stage?: string;
      slideNumber?: number;
      retryable?: boolean;
      exceptionType?: string;
      fingerprint?: string;
    } = {}
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? 0;
    this.code = options.code;
    this.requestId = options.requestId || undefined;
    this.detail = options.detail;
    this.stage = options.stage;
    this.slideNumber = options.slideNumber;
    this.retryable = options.retryable;
    this.exceptionType = options.exceptionType;
    this.fingerprint = options.fingerprint;
  }
}

export const AUTH_SESSION_EXPIRED_EVENT = "presenton:auth-session-expired";

export const isUnauthorizedApiError = (error: unknown): error is ApiError =>
  error instanceof ApiError && error.status === 401;

const signalExpiredAuthSession = (status: number) => {
  if (status !== 401 || typeof window === "undefined") return;
  window.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT));
};

const TEMPLATE_V2_ERROR_MESSAGES: Record<string, string> = {
  template_v2_revision_invalid:
    "선택한 템플릿 리비전이 유효하지 않습니다. 템플릿을 다시 선택해 주세요.",
  template_v2_revision_conflict:
    "선택한 템플릿이 변경되었습니다. 최신 리비전을 선택한 뒤 다시 시도해 주세요.",
  template_v2_source_invalid:
    "구조화 템플릿 소스가 유효하지 않습니다. 템플릿을 복구하거나 다시 게시해 주세요.",
  template_v2_layouts_invalid:
    "구조화 템플릿 레이아웃 스키마가 유효하지 않습니다. 템플릿을 복구하거나 다시 게시해 주세요.",
  template_v2_generation_invalid:
    "생성된 콘텐츠가 구조화 템플릿 스키마와 일치하지 않습니다.",
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
      (TEMPLATE_V2_ERROR_MESSAGES[apiError.code] ||
        IDEMPOTENCY_ERROR_MESSAGES[apiError.code])) ||
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

 
  static async handleResponse(response: Response, defaultErrorMessage: string): Promise<any> {
    // Handle successful responses
    if (response.ok) {
      // Handle 204 No Content responses
      if (response.status === 204) {
        return true;
      }
      
      // Try to parse JSON response
      try {
        return await response.json();
      } catch {
        // If JSON parsing fails but response is ok, return empty object
        return {};
      }
    }

    // Let the application shell replace the stale authenticated page with the
    // login screen. Callers still receive ApiError so their pending work stops.
    signalExpiredAuthSession(response.status);

    // Handle error responses
    let errorMessage = defaultErrorMessage;
    
    try {
      const errorData: ApiErrorResponse = await response.json();
      const structuredError =
        errorData.error && typeof errorData.error === "object"
          ? errorData.error
          : undefined;
      
      // Extract error message in order of preference
      const normalizedDetail = this.normalizeErrorDetail(errorData.detail);
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
        status: structuredError?.status_code ?? response.status,
        code: structuredError?.code,
        requestId:
          structuredError?.request_id ||
          errorData.request_id ||
          response.headers.get("x-request-id") ||
          response.headers.get("x-correlation-id"),
        detail: structuredError?.detail ?? errorData.detail,
        stage: structuredError?.stage,
        slideNumber: structuredError?.slide_number,
        retryable: structuredError?.retryable,
        exceptionType: structuredError?.exception_type,
        fingerprint: structuredError?.fingerprint,
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
        
        // Extract error message in order of preference
        const normalizedDetail = this.normalizeErrorDetail(errorData.detail);
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
