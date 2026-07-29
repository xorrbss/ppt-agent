import PdfMakerPage from "./PdfMakerPage";
import type { PresentationData } from "@/store/slices/presentationGeneration";
import { headers } from "next/headers";
import {
  assertPresentationSnapshotIntegrity,
  normalizeExpectedPresentationSha256,
} from "@/lib/presentation-snapshot-integrity";
import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";

type PdfMakerRouteProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

function firstQueryValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function normalizePresentationId(id: string): string {
  if (!/^[0-9a-f]{32}$/i.test(id)) return id;
  return `${id.slice(0, 8)}-${id.slice(8, 12)}-${id.slice(12, 16)}-${id.slice(16, 20)}-${id.slice(20)}`;
}

async function fetchInitialPresentation(
  id: string,
  cookieHeader: string | undefined,
  expectedSha256: string | undefined
): Promise<PresentationData | undefined> {
  const authHeaders = getFastApiAuthHeaders();
  if (!cookieHeader && Object.keys(authHeaders).length === 0) {
    if (expectedSha256) {
      throw new Error("Authenticated presentation snapshot is unavailable.");
    }
    return undefined;
  }

  const normalizedId = normalizePresentationId(id);
  try {
    const response = await fetch(
      `${getFastApiBaseUrl()}/api/v1/ppt/presentation/${encodeURIComponent(normalizedId)}`,
      {
        headers: {
          ...authHeaders,
          ...(cookieHeader ? { Cookie: cookieHeader } : {}),
        },
        cache: "no-store",
      }
    );
    if (!response.ok) {
      if (expectedSha256) {
        throw new Error(
          `Presentation snapshot fetch failed with status ${response.status}.`
        );
      }
      console.error("[pdf-maker] Initial presentation fetch failed", {
        id: normalizedId,
        status: response.status,
      });
      return undefined;
    }
    const body = await response.text();
    assertPresentationSnapshotIntegrity(body, expectedSha256);
    return JSON.parse(body) as PresentationData;
  } catch (error) {
    if (expectedSha256) throw error;
    console.error("[pdf-maker] Initial presentation fetch failed", {
      id: normalizedId,
      error,
    });
    return undefined;
  }
}

export default async function PdfMakerRoute({ searchParams }: PdfMakerRouteProps) {
  const params = await searchParams;
  const queryId = firstQueryValue(params.id);
  const expectedSha256 = normalizeExpectedPresentationSha256(
    firstQueryValue(params.source_sha256)
  );

  if (!queryId) {
    return (
      <div className="flex h-screen flex-col items-center justify-center">
        <h1 className="text-2xl font-bold">No presentation id found</h1>
        <p className="pb-4 text-gray-500">Please try again</p>
        <a className="rounded bg-blue-600 px-4 py-2 text-white" href="/dashboard">
          Go to home
        </a>
      </div>
    );
  }

  const requestHeaders = await headers();
  const cookieHeader = requestHeaders.get("cookie")?.trim() || undefined;
  const initialPresentationData = await fetchInitialPresentation(
    queryId,
    cookieHeader,
    expectedSha256
  );

  return (
    <PdfMakerPage
      presentation_id={queryId}
      initialPresentationData={initialPresentationData}
    />
  );
}
