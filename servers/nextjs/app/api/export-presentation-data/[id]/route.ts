import { NextRequest, NextResponse } from "next/server";

import {
  ExportPresentationRequestError,
  fetchPersistedPresentationProjectionForExport,
} from "@/lib/export-presentation-route";

const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;
const MAX_PRESENTATION_RESPONSE_BYTES = 50 * 1024 * 1024;

function getFastApiBaseUrl(): string {
  const internal = process.env.FAST_API_INTERNAL_URL?.trim();
  if (internal) {
    return internal.replace(/\/+$/, "");
  }

  const configured = process.env.NEXT_PUBLIC_FAST_API?.trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }

  return "http://127.0.0.1:8000";
}

function normalizePresentationId(id: string): string {
  // The bundled export runtime may compact UUID query parameters before the
  // pdf-maker requests presentation data. FastAPI stores/looks up the canonical
  // hyphenated UUID, so restore that representation without changing non-UUID
  // presentation ids.
  if (!/^[0-9a-f]{32}$/i.test(id)) return id;
  return `${id.slice(0, 8)}-${id.slice(8, 12)}-${id.slice(12, 16)}-${id.slice(16, 20)}-${id.slice(20)}`;
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ id: string }> }
) {
  const { id } = await context.params;
  if (!id) {
    return NextResponse.json(
      { detail: "Missing presentation id" },
      { status: 400 }
    );
  }

  const cookieHeader = request.headers.get("cookie")?.trim();
  if (!cookieHeader) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 });
  }

  const normalizedId = normalizePresentationId(id);
  try {
    const fetched = await fetchPersistedPresentationProjectionForExport(
      normalizedId,
      {
        baseUrl: getFastApiBaseUrl(),
        cookieHeader,
        timeoutMs: PRESENTATION_FETCH_TIMEOUT_MS,
        maxResponseBytes: MAX_PRESENTATION_RESPONSE_BYTES,
      }
    );
    return new NextResponse(fetched.rendererBody, {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    if (error instanceof ExportPresentationRequestError) {
      return NextResponse.json(
        { detail: error.message },
        { status: error.status }
      );
    }
    console.error("[export-presentation-data] Failed to fetch presentation", error);
    return NextResponse.json(
      { detail: "Failed to fetch presentation data" },
      { status: 500 }
    );
  }
}
