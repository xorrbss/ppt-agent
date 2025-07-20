import path from 'path';
import fs from 'fs';
import puppeteer from 'puppeteer';

import { sanitizeFilename } from '@/app/(presentation-generator)/utils/others';
import { NextResponse, NextRequest } from 'next/server';


export async function POST(req: NextRequest) {
  const { id, title } = await req.json(); 
  console.log('path', process.env.APP_DATA_DIRECTORY);
  if (!id) {
    return NextResponse.json({ error: "Missing Presentation ID" }, { status: 400 });
  }
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  await page.goto(`http://localhost/pdf-maker?id=${id}`, { waitUntil: 'networkidle0',timeout: 80000 });

  const pdfBuffer = await page.pdf({
    printBackground: true,
    width: "1280px",
    height: "720px",
    margin: { top: 0, right: 0, bottom: 0, left: 0 }
  });
  browser.close();
  const sanitizedTitle = sanitizeFilename(title);
  const destinationPath = path.join(process.env.APP_DATA_DIRECTORY!,'exports', `${sanitizedTitle}.pdf`);
  console.log('destinationPath', destinationPath);
  await fs.promises.writeFile(destinationPath, pdfBuffer);

  return NextResponse.json({
    success: true,
    path: destinationPath
  });
}
