import { NextRequest, NextResponse } from "next/server";
import path from "path";

import {
  bundledExportPackageAvailable,
  runBundledPresentationExport,
} from "@/lib/run-bundled-presentation-export";
import { resolveAppDataDirectory } from "@/lib/app-data-directory";
import { runAuthoredHybridPresentationExport } from "@/lib/authored-hybrid/export";
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

const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;
const MAX_PRESENTATION_RESPONSE_BYTES = 50 * 1024 * 1024;

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

  try {
    body = await readExportPresentationRouteBody(req);
    const exportResult = await executeExportPresentationRouteRequest(
      body,
      cookieHeader,
      {
        packageAvailable: bundledExportPackageAvailable,
        fetchPresentation: fetchPersistedPresentation,
        registry: {
          general: ({
            format,
            presentationId,
            title,
            cookieHeader,
            expectedPresentationSha256,
          }) =>
            runBundledPresentationExport({
              format,
              presentationId,
              title,
              cookieHeader,
              expectedPresentationSha256,
            }),
          hybrid: ({
            presentationId,
            title,
            cookieHeader,
            fontEmbedding,
          }) =>
            runAuthoredHybridPresentationExport({
              presentationId,
              title,
              cookieHeader,
              fontEmbedding,
            }),
        },
      },
    );

    return NextResponse.json({
      success: true,
      path: buildExportDownloadUrl(exportResult.path),
      ...(exportResult.quality ? { quality: exportResult.quality } : {}),
    });
  } catch (e) {
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
