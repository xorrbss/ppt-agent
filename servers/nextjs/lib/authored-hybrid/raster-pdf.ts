import sharp from "sharp";

const PDF_WIDTH_PT = 960;
const PDF_HEIGHT_PT = 540;
const MAX_RASTER_PDF_BYTES = 512 * 1024 * 1024;

export interface RasterPdfJpegPage {
  readonly jpeg: Buffer;
  readonly width: number;
  readonly height: number;
}

function ascii(value: string): Buffer {
  return Buffer.from(value, "ascii");
}

/** Build a PDF with exactly one full-canvas JPEG XObject per page. */
export function buildRasterPdfFromJpegs(
  pages: readonly RasterPdfJpegPage[],
): Buffer {
  if (!pages.length || pages.length > 100) {
    throw new Error("authored_pdf_slide_count_unsupported");
  }
  for (const page of pages) {
    if (
      !Buffer.isBuffer(page.jpeg) ||
      page.jpeg.length < 4 ||
      !Number.isSafeInteger(page.width) ||
      !Number.isSafeInteger(page.height) ||
      page.width < 1 ||
      page.height < 1
    ) throw new Error("authored_pdf_slide_image_invalid");
  }

  const objectCount = 2 + pages.length * 3;
  const offsets = new Array<number>(objectCount + 1).fill(0);
  const chunks: Buffer[] = [
    Buffer.from("%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", "binary"),
  ];
  let length = chunks[0].length;
  const append = (chunk: Buffer) => {
    chunks.push(chunk);
    length += chunk.length;
    if (length > MAX_RASTER_PDF_BYTES) {
      throw new Error("authored_pdf_too_large");
    }
  };
  const object = (id: number, body: Buffer) => {
    offsets[id] = length;
    append(ascii(`${id} 0 obj\n`));
    append(body);
    append(ascii("\nendobj\n"));
  };

  object(1, ascii("<< /Type /Catalog /Pages 2 0 R >>"));
  const pageIds = pages.map((_page, index) => 3 + index * 3);
  object(
    2,
    ascii(
      `<< /Type /Pages /Count ${pages.length} /Kids [${pageIds
        .map((id) => `${id} 0 R`)
        .join(" ")}] >>`,
    ),
  );

  pages.forEach((page, index) => {
    const pageId = 3 + index * 3;
    const imageId = pageId + 1;
    const contentId = pageId + 2;
    const imageName = `Im${index + 1}`;
    object(
      pageId,
      ascii(
        `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PDF_WIDTH_PT} ${PDF_HEIGHT_PT}] ` +
          `/Resources << /XObject << /${imageName} ${imageId} 0 R >> >> ` +
          `/Contents ${contentId} 0 R >>`,
      ),
    );
    object(
      imageId,
      Buffer.concat([
        ascii(
          `<< /Type /XObject /Subtype /Image /Width ${page.width} /Height ${page.height} ` +
            `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode ` +
            `/Length ${page.jpeg.length} >>\nstream\n`,
        ),
        page.jpeg,
        ascii("\nendstream"),
      ]),
    );
    const paint = ascii(
      `q\n${PDF_WIDTH_PT} 0 0 ${PDF_HEIGHT_PT} 0 0 cm\n/${imageName} Do\nQ\n`,
    );
    object(
      contentId,
      Buffer.concat([
        ascii(`<< /Length ${paint.length} >>\nstream\n`),
        paint,
        ascii("endstream"),
      ]),
    );
  });

  const xrefOffset = length;
  append(ascii(`xref\n0 ${objectCount + 1}\n`));
  append(ascii("0000000000 65535 f \n"));
  for (let id = 1; id <= objectCount; id += 1) {
    append(ascii(`${String(offsets[id]).padStart(10, "0")} 00000 n \n`));
  }
  append(
    ascii(
      `trailer\n<< /Size ${objectCount + 1} /Root 1 0 R >>\n` +
        `startxref\n${xrefOffset}\n%%EOF\n`,
    ),
  );
  return Buffer.concat(chunks);
}

export async function buildRasterPdfFromPngs(
  pngs: readonly Buffer[],
): Promise<Buffer> {
  const pages = await Promise.all(
    pngs.map(async (png) => {
      const converted = await sharp(png)
        .flatten({ background: "#ffffff" })
        .jpeg({ quality: 95, chromaSubsampling: "4:4:4" })
        .toBuffer({ resolveWithObject: true });
      return {
        jpeg: converted.data,
        width: converted.info.width,
        height: converted.info.height,
      };
    }),
  );
  return buildRasterPdfFromJpegs(pages);
}

export function rasterPdfPageCount(pdf: Buffer): number {
  return (pdf.toString("latin1").match(/\/Type \/Page\b/g) ?? []).length;
}
