import { createHash } from "node:crypto";
import { getPublicHeader } from "../app/(presentation-generator)/services/api/header.ts";

import { readBoundedResponseText } from "./authored-hybrid/security.ts";
import {
  PRESENTATION_SOURCE_SHA256,
  type PersistedPresentation,
} from "./presentation-export-strategy.ts";
import { isRecord } from "./guards.ts";

export interface ExportPresentationRouteBody {
  format?: unknown;
  id?: unknown;
  title?: unknown;
  pptxMode?: unknown;
  fontEmbedding?: unknown;
  exportAttemptId?: unknown;
  canonicalSourceDigest?: unknown;
  expectedPresentationRevision?: unknown;
  operationId?: unknown;
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

export interface PersistedPresentationProjectionFetchResult {
  presentation: PersistedPresentation;
  /** Exact JSON handed to the renderer after wire refs are rehydrated. */
  rendererBody: string;
  sourceSha256: string;
  wireBytes: number;
  rendererBytes: number;
}

const PRESENTATION_EXPORT_PROJECTION = "authored-composition-dedup-v1";
const PRESENTATION_EXPORT_PROJECTION_HEADER =
  "x-presenton-presentation-projection";
const COMPOSITION_PIN_REFERENCE_PATH =
  "theme.authored_vnext.compositionPin";
const COMPOSITION_PIN_REFERENCE_SCHEMA =
  "presenton.export-composition-ref/v1";

function isCompositionPinReference(value: unknown): boolean {
  return (
    isRecord(value) &&
    Object.keys(value).length === 2 &&
    value.$presentonRef === COMPOSITION_PIN_REFERENCE_PATH &&
    value.schemaVersion === COMPOSITION_PIN_REFERENCE_SCHEMA
  );
}

function rehydratePresentationExportProjection(
  value: Record<string, unknown>
): void {
  const theme = isRecord(value.theme) ? value.theme : undefined;
  const authoredVnextValue = theme?.authored_vnext;
  const authoredVnext = isRecord(authoredVnextValue)
    ? authoredVnextValue
    : undefined;
  const sharedPinValue = authoredVnext?.compositionPin;
  const sharedPin = isRecord(sharedPinValue)
    ? sharedPinValue
    : undefined;
  const restore = (container: Record<string, unknown> | undefined) => {
    if (!container || !isCompositionPinReference(container.compositionPin)) {
      return;
    }
    if (!sharedPin) {
      throw new ExportPresentationRequestError(
        "Presentation service returned an invalid response",
        502
      );
    }
    container.compositionPin = sharedPin;
  };

  const sceneValue = authoredVnext?.scene;
  restore(isRecord(sceneValue) ? sceneValue : undefined);
  if (Array.isArray(value.slides)) {
    value.slides.forEach((slide) => {
      const content = isRecord(slide) && isRecord(slide.content)
        ? slide.content
        : undefined;
      restore(content);
    });
  }
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

export async function fetchPersistedPresentationProjectionForExport(
  presentationId: string,
  options: PersistedPresentationFetchOptions
): Promise<PersistedPresentationProjectionFetchResult> {
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
        `${options.baseUrl.replace(/\/+$/, "")}/api/v1/ppt/presentation/${encodeURIComponent(presentationId)}/export-projection`,
        {
          method: "GET",
          headers: { ...headers, ...getPublicHeader() },
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
      if (
        error instanceof Error &&
        error.message === "response exceeded the hybrid size limit"
      ) {
        throw new ExportPresentationRequestError(
          `Presentation export projection exceeded the ${options.maxResponseBytes} byte wire limit`,
          413
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
    if (
      response.headers.get(PRESENTATION_EXPORT_PROJECTION_HEADER) !==
      PRESENTATION_EXPORT_PROJECTION
    ) {
      throw new ExportPresentationRequestError(
        "Presentation service returned an invalid response",
        502
      );
    }
    rehydratePresentationExportProjection(parsed as Record<string, unknown>);
    const rendererBody = JSON.stringify(parsed);
    const sourceSha256 = createHash("sha256")
      .update(rendererBody)
      .digest("hex");
    const presentation = parsed as PersistedPresentation;
    Object.defineProperty(presentation, PRESENTATION_SOURCE_SHA256, {
      configurable: false,
      enumerable: false,
      value: sourceSha256,
      writable: false,
    });
    return {
      presentation,
      rendererBody,
      sourceSha256,
      wireBytes: Buffer.byteLength(body, "utf8"),
      rendererBytes: Buffer.byteLength(rendererBody, "utf8"),
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function fetchPersistedPresentationForExport(
  presentationId: string,
  options: PersistedPresentationFetchOptions
): Promise<PersistedPresentation> {
  return (
    await fetchPersistedPresentationProjectionForExport(
      presentationId,
      options
    )
  ).presentation;
}
