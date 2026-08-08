import { NextRequest, NextResponse } from "next/server";
import sharp from "sharp";

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
    return NextResponse.json({ error: "Invalid src" }, { status: 400 });
  }

  const base =
    process.env.FAST_API_INTERNAL_URL?.trim() || "http://127.0.0.1:8000";

  try {
    const resp = await fetch(`${base}${src}`, { cache: "no-store" });
    if (!resp.ok) {
      return NextResponse.json(
        { error: "Icon fetch failed" },
        { status: 502 }
      );
    }
    const svg = Buffer.from(await resp.arrayBuffer());
    // density high enough that small SVGs stay crisp when embedded in a slide.
    const png = await sharp(svg, { density: 384 })
      .resize(256, 256, { fit: "inside", background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toBuffer();

    return new NextResponse(new Uint8Array(png), {
      status: 200,
      headers: {
        "Content-Type": "image/png",
        "Cache-Control": "public, max-age=3600",
      },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
