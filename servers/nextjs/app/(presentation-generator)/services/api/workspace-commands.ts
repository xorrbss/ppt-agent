import { buildAbsoluteApiRequestUrl, getApiUrl } from "@/utils/api";
import { getHeader } from "./header";
import { ROOT, parseResponse, WorkspaceApiError } from "./workspace-core";
import { normalizeEventPage, normalizeTimelinePage, normalizeWorkspaceActionReceipt } from "../../presentation/workspace/timelineProjection";
import type { WorkspaceCapability, WorkspaceActionReceipt, WorkspaceCommandRequest, WorkspaceEventPage, WorkspaceOperationActionRequest, WorkspaceTimelinePage, WorkspaceTemplateApplicationReceipt, WorkspaceTemplateApplicationRequest, WorkspacePartialRecoveryPlan } from "../../presentation/workspace/types";

export async function getWorkspaceCapabilities(
  signal?: AbortSignal,
  presentationId?: string,
): Promise<WorkspaceCapability> {
  try {
    const url = new URL(buildAbsoluteApiRequestUrl(`${ROOT}/capabilities`));
    if (presentationId) url.searchParams.set("presentation_id", presentationId);
    const response = await fetch(url, {
      headers: getHeader(),
      signal,
      cache: "no-store",
    });
    if (response.status === 404 || response.status === 501) {
      return {
        supported: false,
        timeline: false,
        commands: false,
        events: false,
        reason: "unsupported",
      };
    }
    const payload = (await parseResponse(response)) as Record<string, unknown>;
    const data =
      payload.data && typeof payload.data === "object"
        ? (payload.data as Record<string, unknown>)
        : payload;
    const supported = data.supported === true;
    const enabled = data.enabled === true;
    return {
      supported,
      timeline: supported && data.timeline !== false,
      commands: supported && enabled && data.commands !== false,
      events: supported && data.events !== false,
      reason:
        typeof data.reason === "string"
          ? data.reason
          : supported
            ? undefined
            : "disabled",
    };
  } catch (error) {
    if (error instanceof WorkspaceApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    return {
      supported: false,
      timeline: false,
      commands: false,
      events: false,
      reason: "unavailable",
    };
  }
}

export async function getWorkspaceTimeline(
  presentationId: string,
  options: {
    beforePosition?: number;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<WorkspaceTimelinePage> {
  const url = new URL(
    buildAbsoluteApiRequestUrl(
      `${ROOT}/presentations/${encodeURIComponent(presentationId)}/timeline`,
    ),
  );
  if (options.beforePosition !== undefined)
    url.searchParams.set("before_position", String(options.beforePosition));
  url.searchParams.set("limit", String(options.limit ?? 50));
  const response = await fetch(url, {
    headers: getHeader(),
    signal: options.signal,
    cache: "no-store",
  });
  return normalizeTimelinePage(await parseResponse(response));
}

export async function getWorkspaceEvents(
  presentationId: string,
  options: { after?: string; limit?: number; signal?: AbortSignal } = {},
): Promise<WorkspaceEventPage> {
  const url = new URL(
    buildAbsoluteApiRequestUrl(
      `${ROOT}/presentations/${encodeURIComponent(presentationId)}/events`,
    ),
  );
  if (options.after) url.searchParams.set("after", options.after);
  url.searchParams.set("limit", String(options.limit ?? 200));
  const response = await fetch(url, {
    headers: getHeader(),
    signal: options.signal,
    cache: "no-store",
  });
  return normalizeEventPage(await parseResponse(response));
}

export async function postWorkspaceCommand(
  presentationId: string,
  request: WorkspaceCommandRequest,
  signal?: AbortSignal,
): Promise<WorkspaceActionReceipt> {
  const response = await fetch(
    getApiUrl(
      `${ROOT}/presentations/${encodeURIComponent(presentationId)}/commands`,
    ),
    {
      method: "POST",
      headers: { ...getHeader(), "Content-Type": "application/json" },
      body: JSON.stringify({
        command: request.command,
        command_type: request.commandType,
        idempotency_key: request.idempotencyKey,
        expected_revision: request.expectedRevision,
        context: request.context,
        asset_ids: request.assetIds,
      }),
      signal,
    },
  );
  return normalizeWorkspaceActionReceipt(await parseResponse(response));
}

function requiredString(
  payload: Record<string, unknown>,
  key: string,
): string {
  const value = payload[key];
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new WorkspaceApiError(
      `Workspace template application response is missing ${key}`,
      502,
      "invalid_template_application_receipt",
      false,
    );
  }
  return value;
}

export async function postWorkspaceTemplateApplication(
  sourcePresentationId: string,
  request: WorkspaceTemplateApplicationRequest,
  signal?: AbortSignal,
): Promise<WorkspaceTemplateApplicationReceipt> {
  const response = await fetch(
    getApiUrl(
      `${ROOT}/presentations/${encodeURIComponent(sourcePresentationId)}/template-applications`,
    ),
    {
      method: "POST",
      headers: {
        ...getHeader(),
        "Content-Type": "application/json",
        "Idempotency-Key": request.idempotencyKey,
      },
      body: JSON.stringify({
        source_revision: request.sourceRevision,
        prompt: request.prompt,
        authored_style:
          request.authoredStyle.intent === "set"
            ? {
                intent: "set",
                style_id: request.authoredStyle.styleId,
              }
            : { intent: "keep" },
        master:
          request.master.intent === "set"
            ? {
                intent: "set",
                pin: {
                  master_id: request.master.pin.masterId,
                  revision: request.master.pin.revision,
                  digest: request.master.pin.digest,
                },
              }
            : { intent: request.master.intent },
        vision_qa: request.visionQa,
      }),
      signal,
    },
  );
  const raw = (await parseResponse(response)) as Record<string, unknown>;
  const sourceRevision = raw.source_revision;
  const schemaVersion = raw.schema_version;
  if (
    typeof sourceRevision !== "number" ||
    !Number.isFinite(sourceRevision) ||
    typeof schemaVersion !== "number" ||
    !Number.isFinite(schemaVersion)
  ) {
    throw new WorkspaceApiError(
      "Workspace template application response has an invalid revision",
      502,
      "invalid_template_application_receipt",
      false,
    );
  }
  return {
    schemaVersion,
    sourcePresentationId: requiredString(raw, "source_presentation_id"),
    sourceRevision,
    targetPresentationId: requiredString(raw, "target_presentation_id"),
    conversationId: requiredString(raw, "conversation_id"),
    firstUserMessageId: requiredString(raw, "first_user_message_id"),
    operationId: requiredString(raw, "operation_id"),
    status: requiredString(raw, "status"),
    operationState: requiredString(raw, "operation_state"),
    duplicate: raw.duplicate === true,
    timeline: normalizeTimelinePage(raw.timeline),
  };
}

export async function postWorkspaceOperationAction(
  operationId: string,
  request: WorkspaceOperationActionRequest,
  signal?: AbortSignal,
): Promise<WorkspaceActionReceipt> {
  const response = await fetch(
    getApiUrl(`${ROOT}/operations/${encodeURIComponent(operationId)}/actions`),
    {
      method: "POST",
      headers: { ...getHeader(), "Content-Type": "application/json" },
      body: JSON.stringify({
        action: request.action,
        idempotency_key: request.idempotencyKey,
        expected_revision: request.expectedRevision,
        expected_operation_version: request.expectedOperationVersion,
        recovery_revision: request.recoveryRevision,
        recovery_decision: request.recoveryDecision ? {
          manifest_hash: request.recoveryDecision.manifestHash,
          nonce: request.recoveryDecision.nonce,
          acknowledged_unknown_indices: request.recoveryDecision.acknowledgedUnknownIndices,
          additional_budget_microunits: request.recoveryDecision.additionalBudgetMicrounits,
        } : undefined,
      }),
      signal,
    },
  );
  return normalizeWorkspaceActionReceipt(await parseResponse(response));
}

export async function getWorkspacePartialRecoveryPlan(
  operationId: string,
  signal?: AbortSignal,
): Promise<WorkspacePartialRecoveryPlan> {
  const response = await fetch(getApiUrl(`${ROOT}/operations/${encodeURIComponent(operationId)}/recovery-plan`), {
    headers: getHeader(), signal, cache: "no-store",
  });
  const raw = await parseResponse(response) as Record<string, unknown>;
  const data = raw.data && typeof raw.data === "object" ? raw.data as Record<string, unknown> : raw;
  const invalid = () => new WorkspaceApiError("이어 만들기 계획을 확인할 수 없습니다.", 502, "invalid_recovery_plan", false);
  const count = (key: string) => {
    const value = data[key];
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw invalid();
    return value;
  };
  const indices = (key: string) => {
    const value = data[key];
    if (!Array.isArray(value) || value.some((index) => typeof index !== "number" || !Number.isSafeInteger(index) || index < 0)) throw invalid();
    return (value as number[]).slice().sort((left, right) => left - right);
  };
  if (data.recovery_revision !== "svg-partial-recovery/v1" || data.operation_id !== operationId
      || typeof data.presentation_id !== "string" || !data.presentation_id
      || typeof data.manifest_hash !== "string" || !/^[a-f0-9]{64}$/i.test(data.manifest_hash)
      || typeof data.nonce !== "string" || !/^[a-f0-9]{64}$/i.test(data.nonce)
      || typeof data.historical_actual_cost_unbounded !== "boolean") throw invalid();
  const knownIndices = indices("known_indices"), newIndices = indices("new_indices"), unknownIndices = indices("unknown_indices");
  const expectedPageCount = count("expected_page_count");
  const pages = [...knownIndices, ...newIndices, ...unknownIndices].sort((left, right) => left - right);
  const limit = count("additional_budget_limit_microunits");
  const authorCount = count("prospective_author_request_count");
  const remaining = data.proven_remaining_budget_microunits === null ? null : count("proven_remaining_budget_microunits");
  if (expectedPageCount < 1 || limit < 1 || pages.length !== expectedPageCount || pages.some((page, index) => page !== index)) throw invalid();
  if (authorCount !== newIndices.length + unknownIndices.length) throw invalid();
  return {
    recoveryRevision: "svg-partial-recovery/v1", manifestHash: data.manifest_hash, nonce: data.nonce,
    operationId, operationVersion: count("operation_version"), presentationId: data.presentation_id,
    presentationRevision: count("presentation_revision"), expectedPageCount, knownIndices, newIndices, unknownIndices,
    historicalActualCostUnbounded: data.historical_actual_cost_unbounded,
    provenRemainingBudgetMicrounits: remaining, additionalBudgetLimitMicrounits: limit,
    prospectiveAuthorRequestCount: authorCount,
    prospectiveRetryRequestLimit: count("prospective_retry_request_limit"),
  };
}

