import PdfMakerPage from "./PdfMakerPage";
import type { PresentationData } from "@/store/slices/presentationGeneration";
import { headers } from "next/headers";
import {
  assertPresentationSnapshotIntegrity,
  normalizeExpectedPresentationSha256,
} from "@/lib/presentation-snapshot-integrity";
import {
  fetchPersistedPresentationProjectionForExport,
} from "@/lib/export-presentation-route";
import { RENDER_ARTIFACT_CONTRACT } from "@/lib/render-service";
import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";
import { UiLocaleSwitcher } from "@/components/i18n/UiLocaleSwitcher";
import { getServerI18n } from "@/i18n/server";
import { createTranslator } from "next-intl";

const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;
const MAX_PRESENTATION_RESPONSE_BYTES = 50 * 1024 * 1024;

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
  expectedSha256: string | undefined,
  expectedRevision: number | undefined,
  renderArtifactRequired: boolean,
): Promise<PresentationData | undefined> {
  const authHeaders = getFastApiAuthHeaders();
  const authDisabled =
    process.env.DISABLE_AUTH?.trim().toLowerCase() === "true";
  if (!cookieHeader && Object.keys(authHeaders).length === 0 && !authDisabled) {
    if (expectedSha256 || renderArtifactRequired) {
      throw new Error("Authenticated presentation snapshot is unavailable.");
    }
    return undefined;
  }

  const normalizedId = normalizePresentationId(id);
  try {
    const fetched = await fetchPersistedPresentationProjectionForExport(
      normalizedId,
      {
        baseUrl: getFastApiBaseUrl(),
        authHeaders,
        cookieHeader,
        timeoutMs: PRESENTATION_FETCH_TIMEOUT_MS,
        maxResponseBytes: MAX_PRESENTATION_RESPONSE_BYTES,
      }
    );
    const sourceSha256 = assertPresentationSnapshotIntegrity(
      fetched.rendererBody,
      expectedSha256,
    );
    const presentation = fetched.presentation as PresentationData;
    if (
      expectedRevision !== undefined &&
      Number(presentation.revision) !== expectedRevision
    ) {
      throw new Error("stale_render_revision");
    }
    return {
      ...presentation,
      source_sha256: sourceSha256,
    };
  } catch (error) {
    if (expectedSha256 || renderArtifactRequired) throw error;
    console.error("[pdf-maker] Initial presentation fetch failed", {
      id: normalizedId,
      error,
    });
    return undefined;
  }
}

export default async function PdfMakerRoute({ searchParams }: PdfMakerRouteProps) {
  const params = await searchParams;
  const { locale, messages, t } = await getServerI18n();
  const pdfMaker = createTranslator({ locale, messages, namespace: "pdfMaker" });
  const queryId = firstQueryValue(params.id);
  const expectedSha256 = normalizeExpectedPresentationSha256(
    firstQueryValue(params.source_sha256)
  );
  const renderContract = firstQueryValue(params.render_contract);
  const renderArtifactRequired = renderContract === RENDER_ARTIFACT_CONTRACT;
  if (renderContract !== undefined && !renderArtifactRequired) {
    throw new Error("Unsupported presentation render artifact contract.");
  }
  const requestedFormat = firstQueryValue(params.format);
  const expectedRevisionQuery = firstQueryValue(params.expected_revision);
  const expectedRevision =
    expectedRevisionQuery === undefined
      ? undefined
      : Number(expectedRevisionQuery);
  if (
    expectedRevision !== undefined &&
    (!Number.isSafeInteger(expectedRevision) || expectedRevision < 0)
  ) {
    throw new Error("Invalid expected presentation revision.");
  }
  if (renderArtifactRequired && expectedRevision === undefined) {
    throw new Error("An expected revision is required for canonical export.");
  }
  const renderTarget =
    requestedFormat === "pdf" || requestedFormat === "pptx"
      ? requestedFormat
      : undefined;
  if (renderArtifactRequired && !renderTarget) {
    throw new Error("A render target is required for canonical export.");
  }

  if (!queryId) {
    return (
      <div className="flex h-screen flex-col items-center justify-center">
        <UiLocaleSwitcher className="mb-6" />
        <h1 className="text-2xl font-bold">{pdfMaker("missingIdTitle")}</h1>
        <p className="pb-4 text-gray-500">{pdfMaker("missingIdDescription")}</p>
        <a className="rounded bg-blue-600 px-4 py-2 text-white" href="/dashboard">
          {t("common.home")}
        </a>
      </div>
    );
  }

  const requestHeaders = await headers();
  const cookieHeader = requestHeaders.get("cookie")?.trim() || undefined;
  const initialPresentationData = await fetchInitialPresentation(
    queryId,
    cookieHeader,
    expectedSha256,
    expectedRevision,
    renderArtifactRequired,
  );

  return (
    <PdfMakerPage
      presentation_id={queryId}
      initialPresentationData={initialPresentationData}
      renderTarget={renderTarget}
      renderArtifactRequired={renderArtifactRequired}
      expectedRevision={expectedRevision}
    />
  );
}
