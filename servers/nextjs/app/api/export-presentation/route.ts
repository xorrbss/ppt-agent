import { NextRequest, NextResponse } from "next/server";
import path from "path";

import {
  BundledPresentationExportFormat,
  bundledExportPackageAvailable,
  runBundledPresentationExport,
} from "@/lib/run-bundled-presentation-export";
import { resolveAppDataDirectory } from "@/lib/app-data-directory";
import { runAuthoredHybridPresentationExport } from "@/lib/authored-hybrid/export";
import { resolveRequestedPptxMode } from "@/lib/authored-hybrid/mode";

function isValidFormat(value: unknown): value is BundledPresentationExportFormat {
  return value === "pdf" || value === "pptx";
}

function buildExportDownloadUrl(outPath: string): string {
  const appDataDirectory = resolveAppDataDirectory();

  const exportsDirectory = path.join(appDataDirectory, "exports");
  const relativePath = path.relative(exportsDirectory, outPath);
  if (
    !relativePath ||
    relativePath.startsWith("..") ||
    path.isAbsolute(relativePath)
  ) {
    throw new Error("Export finished outside the configured exports directory.");
  }

  return `/api/export-presentation/file?name=${encodeURIComponent(relativePath)}`;
}

export async function POST(req: NextRequest) {
  const { format, id, title, pptxMode } = await req.json();
  const cookieHeader = req.headers.get("cookie") ?? "";

  if (!id) {
    return NextResponse.json(
      { error: "Missing Presentation ID" },
      { status: 400 }
    );
  }

  if (!isValidFormat(format)) {
    return NextResponse.json(
      { error: "Invalid export format" },
      { status: 400 }
    );
  }

  const resolvedPptxMode = resolveRequestedPptxMode(format, pptxMode);
  if (format === "pptx" && !resolvedPptxMode.ok) {
    return NextResponse.json(
      { error: "Invalid PPTX export mode" },
      { status: 400 }
    );
  }

  try {
    if (!(await bundledExportPackageAvailable())) {
      throw new Error(
        "presentation-export runtime is not available. Run scripts/sync-presentation-export.cjs to install it."
      );
    }

    const { path: outPath } =
      format === "pptx" &&
      resolvedPptxMode.ok &&
      resolvedPptxMode.value === "hybrid"
        ? await runAuthoredHybridPresentationExport({
            presentationId: id,
            title,
            cookieHeader,
          })
        : await runBundledPresentationExport({
            format,
            presentationId: id,
            title,
            cookieHeader,
          });

    return NextResponse.json({
      success: true,
      path: buildExportDownloadUrl(outPath),
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    console.error(`[export-presentation:${format}]`, message);
    return NextResponse.json(
      { error: message, success: false },
      { status: 500 }
    );
  }
}
