import fs from "fs/promises";
import path from "path";
import { NextRequest, NextResponse } from "next/server";

const CONTENT_TYPES: Record<string, string> = {
  ".pdf": "application/pdf",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".zip": "application/zip",
};

function getExportsDirectory(): string {
  const appDataDirectory = process.env.APP_DATA_DIRECTORY?.trim();
  if (!appDataDirectory) {
    throw new Error("APP_DATA_DIRECTORY is required to download exported files.");
  }
  return path.join(appDataDirectory, "exports");
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

  try {
    const exportsDirectory = getExportsDirectory();
    const resolvedExportsDirectory = await fs.realpath(exportsDirectory);
    const filePath = path.join(exportsDirectory, filename);
    const resolvedFilePath = await fs.realpath(filePath);

    if (
      resolvedFilePath !== resolvedExportsDirectory &&
      !resolvedFilePath.startsWith(resolvedExportsDirectory + path.sep)
    ) {
      return NextResponse.json({ error: "Access denied" }, { status: 403 });
    }

    const fileBuffer = await fs.readFile(resolvedFilePath);
    const ext = path.extname(filename).toLowerCase();
    return new NextResponse(new Uint8Array(fileBuffer), {
      headers: {
        "Content-Type": CONTENT_TYPES[ext] ?? "application/octet-stream",
        "Content-Disposition": contentDisposition(filename),
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
