import PdfMakerPage from "./PdfMakerPage";
import type { PresentationData } from "@/store/slices/presentationGeneration";
import { headers } from "next/headers";

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

function getFastApiBaseUrl(): string {
  return (
    process.env.FAST_API_INTERNAL_URL?.trim() ||
    process.env.NEXT_PUBLIC_FAST_API?.trim() ||
    "http://127.0.0.1:8000"
  ).replace(/\/+$/, "");
}

async function fetchInitialPresentation(
  id: string,
  cookieHeader: string | undefined
): Promise<PresentationData | undefined> {
  if (!cookieHeader) return undefined;

  const normalizedId = normalizePresentationId(id);
  try {
    const response = await fetch(
      `${getFastApiBaseUrl()}/api/v1/ppt/presentation/${encodeURIComponent(normalizedId)}`,
      {
        headers: { Cookie: cookieHeader },
        cache: "no-store",
      }
    );
    if (!response.ok) {
      console.error("[pdf-maker] Initial presentation fetch failed", {
        id: normalizedId,
        status: response.status,
      });
      return undefined;
    }
    return (await response.json()) as PresentationData;
  } catch (error) {
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
    cookieHeader
  );

  return (
    <PdfMakerPage
      presentation_id={queryId}
      initialPresentationData={initialPresentationData}
    />
  );
}
