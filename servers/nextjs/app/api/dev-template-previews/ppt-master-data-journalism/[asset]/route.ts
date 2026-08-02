import { readFile } from "node:fs/promises";
import path from "node:path";

const ARTIFACT_DIRECTORY = path.resolve(
  process.cwd(),
  "../../docs/artifacts/ppt-master-data-journalism"
);

const ASSETS = {
  "montage.png": {
    contentType: "image/png",
    disposition: "inline; filename=ai-capital-2026-data-journalism-montage.png",
    filename: "ai-capital-2026-data-journalism-montage.png",
  },
  "template.pptx": {
    contentType:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    disposition: "attachment; filename=ai-capital-2026-data-journalism.pptx",
    filename: "ai-capital-2026-data-journalism.pptx",
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
    return new Response(bytes, {
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
