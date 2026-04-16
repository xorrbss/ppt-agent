import path from "path";
import fs from "fs";
import puppeteer from "puppeteer";
import { NextResponse, NextRequest } from "next/server";

import { sanitizeFilename } from "@/app/(presentation-generator)/utils/others";
import {
  bundledExportPackageAvailable,
  runBundledPdfExport,
} from "@/lib/run-bundled-pdf-export";

async function exportPdfWithInlinePuppeteer(
  id: string,
  title: string | undefined
): Promise<{ path: string }> {
  let nextjsUrl = process.env.NEXT_PUBLIC_URL;
  if (!nextjsUrl) {
    nextjsUrl = "http://127.0.0.1";
  }

  const browser = await puppeteer.launch({
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH,
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      "--disable-web-security",
      "--disable-background-timer-throttling",
      "--disable-backgrounding-occluded-windows",
      "--disable-renderer-backgrounding",
      "--disable-features=TranslateUI",
      "--disable-ipc-flooding-protection",
    ],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 720 });
  page.setDefaultNavigationTimeout(300000);
  page.setDefaultTimeout(300000);

  await page.goto(`${nextjsUrl}/pdf-maker?id=${id}`, {
    waitUntil: "networkidle0",
    timeout: 300000,
  });

  await page.waitForFunction('() => document.readyState === "complete"');

  try {
    await page.waitForFunction(
      `
      () => {
        const allElements = document.querySelectorAll('*');
        let loadedElements = 0;
        let totalElements = allElements.length;
        
        for (let el of allElements) {
            const style = window.getComputedStyle(el);
            const isVisible = style.display !== 'none' && 
                            style.visibility !== 'hidden' && 
                            style.opacity !== '0';
            
            if (isVisible && el.offsetWidth > 0 && el.offsetHeight > 0) {
                loadedElements++;
            }
        }
        
        return (loadedElements / totalElements) >= 0.99;
      }
      `,
      { timeout: 300000 }
    );

    await new Promise((resolve) => setTimeout(resolve, 1000));
  } catch (error) {
    console.log("Warning: Some content may not have loaded completely:", error);
  }

  const pdfBuffer = await page.pdf({
    width: "1280px",
    height: "720px",
    printBackground: true,
    margin: { top: 0, right: 0, bottom: 0, left: 0 },
  });

  await browser.close();

  const sanitizedTitle = sanitizeFilename(title ?? "presentation");
  const appDataDirectory = process.env.APP_DATA_DIRECTORY!;
  if (!appDataDirectory) {
    throw new Error("App data directory not found");
  }
  const destinationPath = path.join(
    appDataDirectory,
    "exports",
    `${sanitizedTitle}.pdf`
  );
  await fs.promises.mkdir(path.dirname(destinationPath), { recursive: true });
  await fs.promises.writeFile(destinationPath, pdfBuffer);

  return { path: destinationPath };
}

export async function POST(req: NextRequest) {
  const { id, title } = await req.json();
  if (!id) {
    return NextResponse.json(
      { error: "Missing Presentation ID" },
      { status: 400 }
    );
  }

  try {
    if (await bundledExportPackageAvailable()) {
      const { path: outPath } = await runBundledPdfExport({
        presentationId: id,
        title,
      });
      return NextResponse.json({
        success: true,
        path: outPath,
      });
    }

    const { path: outPath } = await exportPdfWithInlinePuppeteer(id, title);
    return NextResponse.json({
      success: true,
      path: outPath,
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    console.error("[export-as-pdf]", message);
    return NextResponse.json(
      { error: message, success: false },
      { status: 500 }
    );
  }
}
