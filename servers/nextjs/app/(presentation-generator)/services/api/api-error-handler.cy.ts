import {
  ApiError,
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
      "AI 생성 서비스가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요. (요청 ID: request-503)"
    );
  });

  it("does not label a validation error as retryable", () => {
    expect(
      isRetryablePresentationGenerationError(
        new ApiError("Invalid outline", { status: 422 })
      )
    ).to.equal(false);
  });
});
