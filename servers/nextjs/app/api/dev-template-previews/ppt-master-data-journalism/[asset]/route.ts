import { readFile } from "node:fs/promises";
import path from "node:path";

const ARTIFACT_DIRECTORY = path.resolve(
  process.cwd(),
  "../../docs/artifacts/ppt-master-catalog"
);

const ASSETS = {
  "ppt-master-executive-report-montage.png": {
    contentType: "image/png",
    disposition: "inline; filename=ppt-master-executive-report-montage.png",
    filename: "ppt-master-executive-report-montage.png",
  },
  "ppt-master-executive-report.pptx": {
    contentType:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    disposition: "attachment; filename=ppt-master-executive-report.pptx",
    filename: "ppt-master-executive-report.pptx",
  },
  "ppt-master-business-proposal-montage.png": {
    contentType: "image/png",
    disposition: "inline; filename=ppt-master-business-proposal-montage.png",
    filename: "ppt-master-business-proposal-montage.png",
  },
  "ppt-master-business-proposal.pptx": {
    contentType:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    disposition: "attachment; filename=ppt-master-business-proposal.pptx",
    filename: "ppt-master-business-proposal.pptx",
  },
  "ppt-master-data-analysis-report-montage.png": {
    contentType: "image/png",
    disposition: "inline; filename=ppt-master-data-analysis-report-montage.png",
    filename: "ppt-master-data-analysis-report-montage.png",
  },
  "ppt-master-data-analysis-report.pptx": {
    contentType:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    disposition: "attachment; filename=ppt-master-data-analysis-report.pptx",
    filename: "ppt-master-data-analysis-report.pptx",
  },
  "ppt-master-ai-technology-proposal-montage.png": {
    contentType: "image/png",
    disposition: "inline; filename=ppt-master-ai-technology-proposal-montage.png",
    filename: "ppt-master-ai-technology-proposal-montage.png",
  },
  "ppt-master-ai-technology-proposal.pptx": {
    contentType:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    disposition: "attachment; filename=ppt-master-ai-technology-proposal.pptx",
    filename: "ppt-master-ai-technology-proposal.pptx",
  },
  "ppt-master-project-performance-report-montage.png": {
    contentType: "image/png",
    disposition: "inline; filename=ppt-master-project-performance-report-montage.png",
    filename: "ppt-master-project-performance-report-montage.png",
  },
  "ppt-master-project-performance-report.pptx": {
    contentType:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    disposition: "attachment; filename=ppt-master-project-performance-report.pptx",
    filename: "ppt-master-project-performance-report.pptx",
  },
} as const;

type PreviewAsset = keyof typeof ASSETS;

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function isLocalDevelopmentRequest(request: Request): boolean {
  if (process.env.NODE_ENV !== "development") return false;
  const hostname = new URL(request.url).hostname.toLowerCase();
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

export async function GET(
  request: Request,
  context: { params: Promise<{ asset: string }> }
) {
  if (!isLocalDevelopmentRequest(request)) {
    return new Response("Not found", { status: 404 });
  }

  const { asset } = await context.params;
  if (!(asset in ASSETS)) {
    return new Response("Not found", { status: 404 });
  }

  const metadata = ASSETS[asset as PreviewAsset];
  try {
    const bytes = await readFile(path.join(ARTIFACT_DIRECTORY, metadata.filename));
    // `readFile` yields a Buffer over an ArrayBufferLike, which BodyInit does
    // not accept; the constructor copies into a plain ArrayBuffer that it does.
    return new Response(new Uint8Array(bytes), {
      headers: {
        "Cache-Control": "no-store",
        "Content-Disposition": metadata.disposition,
        "Content-Type": metadata.contentType,
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return new Response("Preview artifact unavailable", { status: 404 });
  }
}
