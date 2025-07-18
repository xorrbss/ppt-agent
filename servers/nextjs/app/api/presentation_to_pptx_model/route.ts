import { NextRequest, NextResponse } from "next/server";
import puppeteer from "puppeteer";

export async function POST(request: NextRequest) {
  let id: string;
  try {
    const body = await request.json();
    id = body.id;
  } catch (error) {
    return NextResponse.json({ detail: "Invalid request body" }, { status: 400 });
  }


  return NextResponse.json({ message: "Hello, world!" });
}


async function get_presentation_page(id: string) {
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  await page.goto(`http://localhost/pdf-maker?id=${id}`, {
    waitUntil: "networkidle0",
    timeout: 60000,
  });
  return page;
}