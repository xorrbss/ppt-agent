import {
  ApiError,
  apiErrorFromPayload,
  getApiErrorDisplayMessage,
  getRetryablePresentationGenerationMessage,
  AUTH_SESSION_EXPIRED_EVENT,
  ApiResponseHandler,
  isUnauthorizedApiError,
  isRetryablePresentationGenerationError,
} from "./api-error-handler";

describe("expired authentication sessions", () => {
  it("signals the application shell and returns a classified 401 error", async () => {
    const onExpiredSession = cy.stub();
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, onExpiredSession);

    const response = new Response(JSON.stringify({ detail: "Unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });

    let caught: unknown;
    try {
      await ApiResponseHandler.handleResponse(response, "Request failed");
    } catch (error) {
      caught = error;
    } finally {
      window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, onExpiredSession);
    }

    expect(onExpiredSession).to.have.been.calledOnce;
    expect(isUnauthorizedApiError(caught)).to.equal(true);
    expect(caught).to.include({ status: 401, message: "Unauthorized" });
  });

  it("does not classify unrelated API failures as authentication failures", () => {
    expect(
      isUnauthorizedApiError(new ApiError("Forbidden", { status: 403 }))
    ).to.equal(false);
  });
});

describe("chat-first workspace projection marker", () => {
  it("preserves an explicit projected marker on an API error", async () => {
    const response = new Response(
      JSON.stringify({ detail: "generation_admission_unavailable" }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "X-Chat-First-Workspace-Projected": " true ",
        },
      }
    );

    let caught: unknown;
    try {
      await ApiResponseHandler.handleResponse(response, "Request failed");
    } catch (error) {
      caught = error;
    }

    expect(caught).to.be.instanceOf(ApiError);
    expect(caught).to.include({
      status: 503,
      workspaceProjected: true,
    });
  });

  it("defaults to unprojected when the response marker is absent", async () => {
    const response = new Response(
      JSON.stringify({ detail: "generation_admission_unavailable" }),
      {
        status: 503,
        headers: { "Content-Type": "application/json" },
      }
    );

    let caught: unknown;
    try {
      await ApiResponseHandler.handleResponse(response, "Request failed");
    } catch (error) {
      caught = error;
    }

    expect(caught).to.be.instanceOf(ApiError);
    expect(caught).to.include({
      status: 503,
      workspaceProjected: false,
    });
  });

  it("preserves the projected marker when an error body is not JSON", async () => {
    const response = new Response("upstream unavailable", {
      status: 503,
      headers: {
        "Content-Type": "text/plain",
        "X-Chat-First-Workspace-Projected": "TRUE",
      },
    });

    let caught: unknown;
    try {
      await ApiResponseHandler.handleResponse(response, "Request failed");
    } catch (error) {
      caught = error;
    }

    expect(caught).to.be.instanceOf(ApiError);
    expect(caught).to.include({
      status: 503,
      workspaceProjected: true,
    });
  });

  ["false", "1", "yes", "true-ish"].forEach((marker) => {
    it(`rejects a non-true projected marker value: ${marker}`, async () => {
      const response = new Response(JSON.stringify({ detail: "rejected" }), {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "X-Chat-First-Workspace-Projected": marker,
        },
      });

      let caught: unknown;
      try {
        await ApiResponseHandler.handleResponse(response, "Request failed");
      } catch (error) {
        caught = error;
      }

      expect(caught).to.be.instanceOf(ApiError);
      expect(caught).to.have.property("workspaceProjected", false);
    });
  });
});

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
