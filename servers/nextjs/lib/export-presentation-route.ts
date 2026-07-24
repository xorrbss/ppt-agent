import { createHash } from "node:crypto";

import { resolveRequestedPptxMode } from "./authored-hybrid/mode.ts";
import { readBoundedResponseText } from "./authored-hybrid/security.ts";
import {
  executeExportAtProductionBoundary,
  type ExportBoundaryDependencies,
} from "./presentation-export-boundary.ts";
import {
  PRESENTATION_SOURCE_SHA256,
  type PersistedPresentation,
  type PresentationExportResult,
} from "./presentation-export-strategy.ts";

export interface ExportPresentationRouteBody {
  format?: unknown;
  id?: unknown;
  title?: unknown;
  pptxMode?: unknown;
}

export interface ExportPresentationRouteDependencies<
  Result extends PresentationExportResult = PresentationExportResult,
> extends ExportBoundaryDependencies<Result> {
  packageAvailable(): Promise<boolean>;
}

export class ExportPresentationRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

interface JsonRequest {
  json(): Promise<unknown>;
}

export interface PersistedPresentationFetchOptions {
  baseUrl: string;
  authHeaders?: Record<string, string>;
  cookieHeader?: string;
  timeoutMs: number;
  maxResponseBytes: number;
  fetchImpl?: typeof fetch;
}

function presentationServiceError(status: number): ExportPresentationRequestError {
  if (status === 401) {
    return new ExportPresentationRequestError("Authentication required", 401);
  }
  if (status === 403) {
    return new ExportPresentationRequestError("Presentation access denied", 403);
  }
  if (status === 404) {
    return new ExportPresentationRequestError("Presentation not found", 404);
  }
  if (status === 429) {
    return new ExportPresentationRequestError(
      "Presentation service is rate limited",
      429
    );
  }
  if (status === 400 || status === 422) {
    return new ExportPresentationRequestError(
      "Presentation request was rejected",
      400
    );
  }
  return new ExportPresentationRequestError(
    "Presentation service request failed",
    502
  );
}

function isAbortError(error: unknown): boolean {
  return (
    error instanceof Error && error.name === "AbortError"
  );
}

export async function readExportPresentationRouteBody(
  request: JsonRequest
): Promise<ExportPresentationRouteBody> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    throw new ExportPresentationRequestError("Invalid JSON body", 400);
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    throw new ExportPresentationRequestError("Invalid JSON body", 400);
  }
  return body as ExportPresentationRouteBody;
}

export async function fetchPersistedPresentationForExport(
  presentationId: string,
  options: PersistedPresentationFetchOptions
): Promise<PersistedPresentation> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs);
  const fetchImpl = options.fetchImpl ?? fetch;
  try {
    let response: Response;
    try {
      const headers: Record<string, string> = {
        Accept: "application/json",
        ...options.authHeaders,
      };
      if (options.cookieHeader?.trim()) {
        headers.Cookie = options.cookieHeader;
      }
      response = await fetchImpl(
        `${options.baseUrl.replace(/\/+$/, "")}/api/v1/ppt/presentation/${encodeURIComponent(presentationId)}`,
        {
          method: "GET",
          headers,
          cache: "no-store",
          signal: controller.signal,
        }
      );
    } catch (error) {
      if (isAbortError(error)) {
        throw new ExportPresentationRequestError(
          "Presentation service timed out",
          504
        );
      }
      throw new ExportPresentationRequestError(
        "Presentation service request failed",
        502
      );
    }

    if (!response.ok) {
      throw presentationServiceError(response.status);
    }

    let body: string;
    try {
      body = await readBoundedResponseText(
        response,
        options.maxResponseBytes
      );
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) {
        throw new ExportPresentationRequestError(
          "Presentation service timed out",
          504
        );
      }
      throw new ExportPresentationRequestError(
        "Presentation service returned an invalid response",
        502
      );
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(body);
    } catch {
      throw new ExportPresentationRequestError(
        "Presentation service returned an invalid response",
        502
      );
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new ExportPresentationRequestError(
        "Presentation service returned an invalid response",
        502
      );
    }
    const presentation = parsed as PersistedPresentation;
    Object.defineProperty(presentation, PRESENTATION_SOURCE_SHA256, {
      configurable: false,
      enumerable: false,
      value: createHash("sha256").update(body).digest("hex"),
      writable: false,
    });
    return presentation;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Production route orchestration kept framework-free so the exact request to
 * the persisted-identity export boundary can be exercised with runner spies.
 */
export async function executeExportPresentationRouteRequest<
  Result extends PresentationExportResult,
>(
  body: ExportPresentationRouteBody,
  cookieHeader: string,
  dependencies: ExportPresentationRouteDependencies<Result>
): Promise<Result> {
  if (typeof body.id !== "string" || !body.id) {
    throw new ExportPresentationRequestError("Missing Presentation ID", 400);
  }
  if (body.format !== "pdf" && body.format !== "pptx") {
    throw new ExportPresentationRequestError("Invalid export format", 400);
  }

  const resolvedPptxMode = resolveRequestedPptxMode(
    body.format,
    body.pptxMode
  );
  if (!resolvedPptxMode.ok) {
    throw new ExportPresentationRequestError(
      "Invalid PPTX export mode",
      400
    );
  }
  if (!(await dependencies.packageAvailable())) {
    throw new Error(
      "presentation-export runtime is not available. Run scripts/sync-presentation-export.cjs to install it."
    );
  }

  return executeExportAtProductionBoundary(
    {
      format: body.format,
      presentationId: body.id,
      title: typeof body.title === "string" ? body.title : undefined,
      cookieHeader,
      pptxMode: resolvedPptxMode.value,
    },
    dependencies
  );
}
