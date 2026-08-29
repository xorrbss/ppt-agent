import { NextRequest, NextResponse } from "next/server";
import sharp from "sharp";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_SVG_BYTES = 2 * 1024 * 1024;
const MAX_PNG_BYTES = 4 * 1024 * 1024;

function errorResponse(status: 400 | 413 | 415 | 502) {
  const message = status === 400 ? "Invalid icon request" : "Icon rasterization failed";
  return NextResponse.json(
    { error: message },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

/**
 * Rasterize a server-served SVG icon to PNG. The bundled "editable PPTX" export
 * converter embeds images via Pillow, which cannot read SVG (or data-URIs), so
 * the /pdf-maker export view rewrites SVG icon <img> srcs to this route and the
 * converter downloads a real PNG it can embed.
 *
 * `src` must be a same-app icon path (/static/... or /app_data/...) so this is
 * not a general image proxy.
 */
export async function GET(req: NextRequest) {
  const src = req.nextUrl.searchParams.get("src");
  if (!src || !/^\/(static|app_data)\/[^?#]+\.svg(\?|$)/i.test(src)) {
    return errorResponse(400);
  }

  const base =
    process.env.FAST_API_INTERNAL_URL?.trim() || "http://127.0.0.1:8000";

  try {
    const headers = new Headers({ Accept: "image/svg+xml" });
    const cookie = req.headers.get("cookie");
    const authorization = req.headers.get("authorization");
    // The upstream URL is fixed by server configuration. Forward only the two
    // credentials FastAPI accepts so owner-scoped /app_data remains protected.
    if (cookie) headers.set("Cookie", cookie);
    if (authorization) headers.set("Authorization", authorization);
    const resp = await fetch(`${base}${src}`, {
      cache: "no-store",
      headers,
      redirect: "error",
    });
    if (!resp.ok) {
      return errorResponse(502);
    }
    const mediaType = (resp.headers.get("content-type") || "")
      .split(";", 1)[0]
      .trim()
      .toLowerCase();
    if (mediaType !== "image/svg+xml") return errorResponse(415);
    const declaredLength = Number(resp.headers.get("content-length"));
    if (Number.isFinite(declaredLength) && declaredLength > MAX_SVG_BYTES) {
      return errorResponse(413);
    }
    const svg = Buffer.from(await resp.arrayBuffer());
    if (svg.length === 0 || svg.length > MAX_SVG_BYTES) return errorResponse(413);
    // density high enough that small SVGs stay crisp when embedded in a slide.
    const png = await sharp(svg, { density: 384 })
      .resize(256, 256, { fit: "inside", background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toBuffer();
    if (png.length > MAX_PNG_BYTES) return errorResponse(413);

    return new NextResponse(new Uint8Array(png), {
      status: 200,
      headers: {
        "Content-Type": "image/png",
        "Cache-Control": src.startsWith("/app_data/")
          ? "private, no-store"
          : "public, max-age=3600",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return errorResponse(502);
  }
}
