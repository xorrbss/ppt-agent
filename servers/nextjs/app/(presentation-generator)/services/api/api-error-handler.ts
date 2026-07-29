// API Error Response Interface
interface StructuredApiError {
  status_code?: number;
  detail?: unknown;
  code?: string;
  message?: string;
  request_id?: string | null;
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

  constructor(
    message: string,
    options: {
      status?: number;
      code?: string;
      requestId?: string | null;
      detail?: unknown;
    } = {}
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status ?? 0;
    this.code = options.code;
    this.requestId = options.requestId || undefined;
    this.detail = options.detail;
  }
}

const TEMPLATE_V2_ERROR_MESSAGES: Record<string, string> = {
  template_v2_revision_invalid:
    "The selected template revision is invalid. Please select the template again.",
  template_v2_revision_conflict:
    "The selected template has changed. Please select the latest revision and retry.",
  template_v2_source_invalid:
    "The structured template source is invalid. Please repair or republish the template.",
  template_v2_layouts_invalid:
    "The structured template layout schema is invalid. Please repair or republish the template.",
  template_v2_generation_invalid:
    "The generated content did not match the structured template schema.",
};

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
    (apiError.code && TEMPLATE_V2_ERROR_MESSAGES[apiError.code]) ||
    apiError.message ||
    fallbackMessage;
  return apiError.requestId
    ? `${message} (Request ID: ${apiError.requestId})`
    : message;
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
        return "Bad request. Please check your input and try again.";
      case 401:
        return "Unauthorized. Please log in and try again.";
      case 403:
        return "Access forbidden. You don't have permission to perform this action.";
      case 404:
        return "Resource not found. The requested item may have been deleted or moved.";
      case 409:
        return "Conflict. The resource already exists or there's a conflict with the current state.";
      case 422:
        return "Validation error. Please check your input and try again.";
      case 429:
        return "Too many requests. Please wait a moment and try again.";
      case 500:
        return "Internal server error. Please try again later.";
      case 502:
        return "Bad gateway. The server is temporarily unavailable.";
      case 503:
        return "Service unavailable. Please try again later.";
      case 504:
        return "Gateway timeout. The request took too long to process.";
      default:
        return defaultMessage;
    }
  }
}

export type { ApiErrorResponse, StructuredApiError };
