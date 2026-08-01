import {
  ApiError,
  apiErrorFromPayload,
  getApiErrorDisplayMessage,
  getRetryablePresentationGenerationMessage,
  isRetryablePresentationGenerationError,
} from "./api-error-handler";

describe("retryable presentation generation errors", () => {
  it("identifies a retryable 503 and keeps its request ID in Korean guidance", () => {
    const error = new ApiError("Our servers are currently overloaded.", {
      status: 503,
      code: "generation_retryable",
      requestId: "request-503",
    });

    expect(isRetryablePresentationGenerationError(error)).to.equal(true);
    expect(getRetryablePresentationGenerationMessage(error)).to.equal(
      "AI 생성 서비스가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요. (요청 ID: request-503)",
    );
  });

  it("does not label a validation error as retryable", () => {
    expect(
      isRetryablePresentationGenerationError(
        new ApiError("Invalid outline", { status: 422 }),
      ),
    ).to.equal(false);
  });

  it("preserves slide failure metadata and honors an explicit retryable flag", () => {
    const error = apiErrorFromPayload(
      {
        status_code: 500,
        code: "slide_render_failed",
        message: "Browser render failed",
        stage: "render",
        slide_number: 47,
        retryable: false,
        exception_type: "PlaywrightError",
        fingerprint: "failure-fingerprint",
      },
      "Generation failed"
    );

    expect(error).to.include({
      status: 500,
      code: "slide_render_failed",
      stage: "render",
      slideNumber: 47,
      retryable: false,
      exceptionType: "PlaywrightError",
      fingerprint: "failure-fingerprint",
    });
    expect(isRetryablePresentationGenerationError(error)).to.equal(false);
  });

  it("shows safe slide and stage metadata for a retryable checkpoint failure", () => {
    const error = apiErrorFromPayload(
      {
        status_code: 500,
        code: "slide_render_failed",
        message: "Browser render failed",
        stage: "render",
        slide_number: 47,
        retryable: true,
        exception_type: "PlaywrightError",
        fingerprint: "internal-fingerprint",
      },
      "Generation failed"
    );

    const message = getRetryablePresentationGenerationMessage(error);
    expect(message).to.include("47번 슬라이드");
    expect(message).to.include("렌더링 단계");
    expect(message).not.to.include("PlaywrightError");
    expect(message).not.to.include("internal-fingerprint");
  });
});

describe("idempotency conflict errors", () => {
  it("maps a payload-conflict 409 to Korean guidance with its request ID", () => {
    const error = new ApiError("idempotency_key_payload_conflict", {
      status: 409,
      code: "idempotency_key_payload_conflict",
      requestId: "conflict-409",
    });

    expect(isRetryablePresentationGenerationError(error)).to.equal(false);
    expect(getApiErrorDisplayMessage(error, "Generation failed")).to.equal(
      "이전에 중단된 생성과 요청 내용이 달라 이어서 생성할 수 없습니다. 생성을 다시 실행하면 새 작업으로 처음부터 생성됩니다. (Request ID: conflict-409)",
    );
  });

  it("maps an incomplete idempotency record 409 to Korean guidance", () => {
    const error = new ApiError("idempotency_record_incomplete", {
      status: 409,
      code: "idempotency_record_incomplete",
    });

    expect(getApiErrorDisplayMessage(error, "Generation failed")).to.equal(
      "이전 생성 기록이 불완전하여 이어서 생성할 수 없습니다. 생성을 다시 실행하면 새 작업으로 처음부터 생성됩니다.",
    );
  });

  it("maps an exhausted resume-attempts 409 to Korean guidance", () => {
    const error = new ApiError("resume_attempts_exhausted", {
      status: 409,
      code: "resume_attempts_exhausted",
    });

    expect(isRetryablePresentationGenerationError(error)).to.equal(false);
    expect(getApiErrorDisplayMessage(error, "Generation failed")).to.equal(
      "재개 시도 횟수를 초과하여 이어서 생성할 수 없습니다. 생성을 다시 실행하면 새 작업으로 처음부터 생성됩니다.",
    );
  });
});
