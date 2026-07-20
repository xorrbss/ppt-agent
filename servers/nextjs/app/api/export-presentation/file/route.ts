import fs from "fs";
import fsPromises from "fs/promises";
import path from "path";
import { Readable } from "stream";
import { NextRequest, NextResponse } from "next/server";
import { resolveAppDataDirectory } from "@/lib/app-data-directory";

const CONTENT_TYPES: Record<string, string> = {
  ".pdf": "application/pdf",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".zip": "application/zip",
};

function getExportsDirectory(): string {
  return path.join(resolveAppDataDirectory(), "exports");
}

function getSafeExportName(request: NextRequest): string | null {
  const decodedName = request.nextUrl.searchParams.get("name");

  if (
    !decodedName ||
    decodedName.includes("/") ||
    decodedName.includes("\\") ||
    decodedName !== path.basename(decodedName)
  ) {
    return null;
  }

  return decodedName;
}

function contentDisposition(filename: string): string {
  const fallback = filename.replace(/[^a-zA-Z0-9._-]/g, "_");
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encodeURIComponent(filename)}`;
}

export async function GET(request: NextRequest) {
  const filename = getSafeExportName(request);
  if (!filename) {
    return NextResponse.json({ error: "Invalid export file name" }, { status: 400 });
  }
  const ext = path.extname(filename).toLowerCase();
  if (!CONTENT_TYPES[ext]) {
    return NextResponse.json(
      { error: "Unsupported export file type" },
      { status: 400 }
    );
  }

  try {
    const exportsDirectory = getExportsDirectory();
    const resolvedExportsDirectory = await fsPromises.realpath(exportsDirectory);
    const filePath = path.join(exportsDirectory, filename);
    const resolvedFilePath = await fsPromises.realpath(filePath);

    if (
      resolvedFilePath !== resolvedExportsDirectory &&
      !resolvedFilePath.startsWith(resolvedExportsDirectory + path.sep)
    ) {
      return NextResponse.json({ error: "Access denied" }, { status: 403 });
    }

    const stats = await fsPromises.stat(resolvedFilePath);
    if (!stats.isFile()) {
      return NextResponse.json({ error: "Export file not found" }, { status: 404 });
    }
    const stream = Readable.toWeb(fs.createReadStream(resolvedFilePath));
    return new NextResponse(stream as unknown as BodyInit, {
      headers: {
        "Content-Type": CONTENT_TYPES[ext],
        "Content-Disposition": contentDisposition(filename),
        "Content-Length": String(stats.size),
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException)?.code === "ENOENT") {
      return NextResponse.json({ error: "Export file not found" }, { status: 404 });
    }

    console.error("[export-presentation:file]", error);
    return NextResponse.json(
      { error: "Failed to download export file" },
      { status: 500 }
    );
  }
}
