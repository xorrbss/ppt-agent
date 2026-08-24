import { NextRequest, NextResponse } from "next/server";
import { createHash, randomUUID } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import path from "path";

import {
  bundledExportPackageAvailable,
  runBundledPresentationExport,
} from "@/lib/run-bundled-presentation-export";
import { resolveAppDataDirectory } from "@/lib/app-data-directory";
import {
  runAuthoredFidelityPresentationExport,
  runAuthoredHybridPresentationExport,
} from "@/lib/authored-hybrid/export";
import {
  executeExportPresentationRouteRequest,
  ExportPresentationRequestError,
  fetchPersistedPresentationForExport,
  readExportPresentationRouteBody,
  type ExportPresentationRouteBody,
} from "@/lib/export-presentation-route";
import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";
import {
  PRESENTATION_SOURCE_SHA256,
  resolvePersistedExportStrategy,
} from "@/lib/presentation-export-strategy";

const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;
const MAX_PRESENTATION_RESPONSE_BYTES = 50 * 1024 * 1024;

type HybridAttemptBinding = {
  exportAttemptId: string;
  presentationRevision: number;
  canonicalSourceDigest: string;
  rendererSourceDigest: string;
  htmlSourceDigest: string;
  compositionDigest: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
}

function isHybridPptxRequest(body: ExportPresentationRouteBody): boolean {
  return body.format === "pptx" && body.pptxMode === "hybrid";
}

function isFidelityPptxRequest(body: ExportPresentationRouteBody): boolean {
  return body.format === "pptx" && body.pptxMode === "fidelity";
}

async function postHybridAttempt(
  pathname: string,
  cookieHeader: string,
  body: Record<string, unknown>,
  attemptMode: "hybrid" | "fidelity" = "hybrid",
): Promise<unknown> {
  const modeLabel = attemptMode === "hybrid" ? "Hybrid" : "Fidelity";
  let response: Response;
  try {
    response = await fetch(`${getFastApiBaseUrl()}${pathname}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...getFastApiAuthHeaders(),
        ...(cookieHeader.trim() ? { Cookie: cookieHeader } : {}),
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ExportPresentationRequestError(
      `${modeLabel} export attempt service is unavailable`,
      502,
    );
  }
  if (!response.ok) {
    throw new ExportPresentationRequestError(
      `${modeLabel} export attempt was rejected`,
      response.status === 401 || response.status === 403 ? response.status : 502,
    );
  }
  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch {
    throw new ExportPresentationRequestError(
      `${modeLabel} export attempt returned an invalid response`,
      502,
    );
  }
}

function readHybridAttemptBinding(
  value: unknown,
  attemptMode: "hybrid" | "fidelity" = "hybrid",
): HybridAttemptBinding {
  if (
    !isRecord(value) ||
    typeof value.exportAttemptId !== "string" ||
    !value.exportAttemptId.trim() ||
    !Number.isSafeInteger(value.presentationRevision) ||
    !isSha256(value.canonicalSourceDigest) ||
    !isSha256(value.rendererSourceDigest) ||
    !isSha256(value.htmlSourceDigest) ||
    !isSha256(value.compositionDigest)
  ) {
    throw new ExportPresentationRequestError(
      `${attemptMode === "hybrid" ? "Hybrid" : "Fidelity"} export attempt returned an invalid response`,
      502,
    );
  }
  return {
    exportAttemptId: value.exportAttemptId.trim(),
    presentationRevision: value.presentationRevision,
    canonicalSourceDigest: value.canonicalSourceDigest.toLowerCase(),
    rendererSourceDigest: value.rendererSourceDigest.toLowerCase(),
    htmlSourceDigest: value.htmlSourceDigest.toLowerCase(),
    compositionDigest: value.compositionDigest.toLowerCase(),
  };
}

async function exportedPptxDigest(outPath: string): Promise<{
  pptxSha256: string;
  pptxSizeBytes: number;
}> {
  const [content, fileStat] = await Promise.all([readFile(outPath), stat(outPath)]);
  if (!fileStat.isFile()) throw new Error("export_output_not_file");
  return {
    pptxSha256: createHash("sha256").update(content).digest("hex"),
    pptxSizeBytes: fileStat.size,
  };
}

function buildExportDownloadUrl(outPath: string): string {
  const appDataDirectory = resolveAppDataDirectory();

  const exportsDirectory = path.join(appDataDirectory, "exports");
  const relativePath = path.relative(exportsDirectory, outPath);
  if (
    !relativePath ||
    relativePath.startsWith("..") ||
    path.isAbsolute(relativePath)
  ) {
    throw new Error("Export finished outside the configured exports directory.");
  }

  return `/api/export-presentation/file?name=${encodeURIComponent(relativePath)}`;
}

async function fetchPersistedPresentation(
  presentationId: string,
  cookieHeader: string
): ReturnType<typeof fetchPersistedPresentationForExport> {
  return fetchPersistedPresentationForExport(presentationId, {
    baseUrl: getFastApiBaseUrl(),
    authHeaders: getFastApiAuthHeaders(),
    cookieHeader,
    timeoutMs: PRESENTATION_FETCH_TIMEOUT_MS,
    maxResponseBytes: MAX_PRESENTATION_RESPONSE_BYTES,
  });
}

export async function POST(req: NextRequest) {
  const cookieHeader = req.headers.get("cookie") ?? "";
  let body: ExportPresentationRouteBody | undefined;
  let routeOwnedAttempt: HybridAttemptBinding | undefined;
  let routeOwnedAttemptMode: "hybrid" | "fidelity" | undefined;
  let completedAttemptReceipt: unknown;
  let completedAttempt = false;
  let completedPowerPointQa: unknown;

  try {
    body = await readExportPresentationRouteBody(req);
    let exportBody = body;
    let cachedPresentation:
      | Awaited<ReturnType<typeof fetchPersistedPresentation>>
      | undefined;

    // Direct browser Authored PPTX exports begin a durable attempt against the
    // exact persisted snapshot consumed by the renderer. Callers arriving
    // from FastAPI with a Hybrid binding must not create a duplicate attempt.
    const requestedAttemptMode = isHybridPptxRequest(body)
      ? "hybrid"
      : isFidelityPptxRequest(body)
        ? "fidelity"
        : undefined;
    if (
      requestedAttemptMode &&
      body.exportAttemptId === undefined &&
      body.canonicalSourceDigest === undefined &&
      typeof body.id === "string" &&
      body.id
    ) {
      cachedPresentation = await fetchPersistedPresentation(
        body.id,
        cookieHeader,
      );
      if (resolvePersistedExportStrategy(cachedPresentation) === "authored-hybrid") {
        const rendererSourceSha256 = cachedPresentation[PRESENTATION_SOURCE_SHA256];
        const expectedPresentationRevision =
          typeof body.expectedPresentationRevision === "number"
            ? body.expectedPresentationRevision
            : cachedPresentation.revision;
        if (
          !isSha256(rendererSourceSha256) ||
          !Number.isSafeInteger(expectedPresentationRevision)
        ) {
          throw new ExportPresentationRequestError(
            `${requestedAttemptMode === "hybrid" ? "Hybrid" : "Fidelity"} export attempt source is invalid`,
            409,
          );
        }
        routeOwnedAttempt = readHybridAttemptBinding(
          await postHybridAttempt(
            `/api/v1/ppt/presentation/${encodeURIComponent(body.id)}/export-attempts/${requestedAttemptMode}/begin`,
            cookieHeader,
            {
              expectedPresentationRevision,
              rendererSourceSha256,
              idempotencyKey: randomUUID(),
            },
            requestedAttemptMode,
          ),
          requestedAttemptMode,
        );
        routeOwnedAttemptMode = requestedAttemptMode;
        if (routeOwnedAttempt.rendererSourceDigest !== rendererSourceSha256) {
          throw new ExportPresentationRequestError(
            `${requestedAttemptMode === "hybrid" ? "Hybrid" : "Fidelity"} export attempt source binding mismatch`,
            409,
          );
        }
        exportBody = {
          ...body,
          exportAttemptId: routeOwnedAttempt.exportAttemptId,
          canonicalSourceDigest: routeOwnedAttempt.canonicalSourceDigest,
          operationId: routeOwnedAttempt.exportAttemptId,
        };
      }
    }
    const exportResult = await executeExportPresentationRouteRequest(
      exportBody,
      cookieHeader,
      {
        packageAvailable: bundledExportPackageAvailable,
        fetchPresentation: async (presentationId, requestCookieHeader) =>
          cachedPresentation ??
          fetchPersistedPresentation(presentationId, requestCookieHeader),
        registry: {
          general: ({
            format,
            presentationId,
            title,
            cookieHeader,
            expectedPresentationSha256,
            expectedPresentationRevision,
          }) =>
            runBundledPresentationExport({
              format,
              presentationId,
              title,
              cookieHeader,
              expectedPresentationSha256,
              expectedPresentationRevision,
            }),
          authoredFidelity: ({
            presentationId,
            title,
            cookieHeader,
            exportAttemptId,
            canonicalSourceDigest,
            expectedPresentationRevision,
            explicitPptxMode,
          }) =>
            runAuthoredFidelityPresentationExport({
              presentationId,
              title,
              cookieHeader,
              exportAttemptId,
              canonicalSourceDigest,
              expectedPresentationRevision,
              explicitPptxMode,
            }),
          hybrid: ({
            presentationId,
            title,
            cookieHeader,
            fontEmbedding,
            exportAttemptId,
            canonicalSourceDigest,
            expectedPresentationRevision,
            operationId,
          }) =>
            runAuthoredHybridPresentationExport({
              presentationId,
              title,
              cookieHeader,
              fontEmbedding,
              exportAttemptId,
              canonicalSourceDigest,
              expectedPresentationRevision,
              operationId,
            }),
        },
      },
    );

    const coverage = exportResult.exportMetadata?.coverageManifest;
    let responseQuality = exportResult.quality;
    let responseExportMetadata = exportResult.exportMetadata;
    if (routeOwnedAttempt && routeOwnedAttemptMode === "hybrid") {
      const coverageSource = coverage?.source as
        | {
            exportAttemptId?: string;
            canonicalSourceDigest?: string;
            sourceDigest?: string;
          }
        | undefined;
      if (
        !exportResult.quality ||
        !coverage ||
        coverageSource?.exportAttemptId !== routeOwnedAttempt.exportAttemptId ||
        coverageSource.canonicalSourceDigest !==
          routeOwnedAttempt.canonicalSourceDigest ||
        coverageSource.sourceDigest !== routeOwnedAttempt.rendererSourceDigest
      ) {
        throw new ExportPresentationRequestError(
          "Hybrid export evidence is incomplete",
          409,
        );
      }
      const { pptxSha256, pptxSizeBytes } = await exportedPptxDigest(
        exportResult.path,
      );
      const completed = await postHybridAttempt(
        `/api/v1/ppt/presentation/${encodeURIComponent(String(exportBody.id))}/export-attempts/hybrid/${encodeURIComponent(routeOwnedAttempt.exportAttemptId)}/complete`,
        cookieHeader,
        {
          pptxSha256,
          pptxSizeBytes,
          artifactPath: exportResult.path,
          quality: exportResult.quality,
          coverage,
        },
      );
      completedPowerPointQa = isRecord(completed)
        ? completed.powerPointQa
        : undefined;
      if (isRecord(completed) && isRecord(completed.quality)) {
        responseQuality = completed.quality as typeof exportResult.quality;
      }
      if (isRecord(completed) && isRecord(completed.coverage)) {
        responseExportMetadata = {
          ...exportResult.exportMetadata,
          coverageManifest: completed.coverage,
        } as typeof exportResult.exportMetadata;
      }
      completedAttempt = true;
    } else if (routeOwnedAttempt && routeOwnedAttemptMode === "fidelity") {
      const { pptxSha256, pptxSizeBytes } = await exportedPptxDigest(
        exportResult.path,
      );
      completedAttemptReceipt = await postHybridAttempt(
        `/api/v1/ppt/presentation/${encodeURIComponent(String(exportBody.id))}/export-attempts/fidelity/${encodeURIComponent(routeOwnedAttempt.exportAttemptId)}/complete`,
        cookieHeader,
        {
          pptxSha256,
          pptxSizeBytes,
          artifactPath: exportResult.path,
        },
        "fidelity",
      );
      completedAttempt = true;
    }

    const embedding = responseQuality?.fontEmbeddingStatus;
    if (
      exportResult.exportMetadata?.strategy === "authored-hybrid" &&
      exportBody.fontEmbedding !== false &&
      (!embedding?.requested || !embedding.applied)
    ) {
      return NextResponse.json(
        {
          success: false,
          error: "Font embedding was requested but was not applied",
          ...(responseQuality ? { quality: responseQuality } : {}),
          ...(responseExportMetadata
            ? { exportMetadata: responseExportMetadata }
            : {}),
          ...(routeOwnedAttempt
            ? { exportAttempt: completedAttemptReceipt ?? routeOwnedAttempt }
            : {}),
          ...(completedPowerPointQa ? { powerPointQa: completedPowerPointQa } : {}),
        },
        { status: 409 },
      );
    }

    return NextResponse.json({
      success: true,
      path: buildExportDownloadUrl(exportResult.path),
      ...(responseQuality ? { quality: responseQuality } : {}),
      ...(responseExportMetadata
        ? { exportMetadata: responseExportMetadata }
        : {}),
      ...(routeOwnedAttempt
        ? { exportAttempt: completedAttemptReceipt ?? routeOwnedAttempt }
        : {}),
      ...(completedPowerPointQa ? { powerPointQa: completedPowerPointQa } : {}),
    });
  } catch (e) {
    if (routeOwnedAttempt && !completedAttempt && typeof body?.id === "string") {
      try {
        await postHybridAttempt(
          `/api/v1/ppt/presentation/${encodeURIComponent(body.id)}/export-attempts/${routeOwnedAttemptMode ?? "hybrid"}/${encodeURIComponent(routeOwnedAttempt.exportAttemptId)}/fail`,
          cookieHeader,
          {
            failureCode:
              routeOwnedAttemptMode === "fidelity"
                ? "next_fidelity_export_failed"
                : "next_hybrid_export_failed",
          },
          routeOwnedAttemptMode ?? "hybrid",
        );
      } catch {
        // The original export failure remains the client-visible error.
      }
    }
    if (e instanceof ExportPresentationRequestError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    const message = e instanceof Error ? e.message : String(e);
    console.error(
      `[export-presentation:${String(body?.format ?? "unknown")}]`,
      message
    );
    return NextResponse.json(
      { error: "Presentation export failed", success: false },
      { status: 500 }
    );
  }
}
