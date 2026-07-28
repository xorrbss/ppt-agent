import { NextRequest, NextResponse } from "next/server";
import path from "path";
import fs from "fs";
import crypto from "crypto";

import {
  formatUploadLimit,
  getUploadLimits,
} from "@/lib/upload-limits";

const MULTIPART_OVERHEAD_BYTES = 1024 * 1024;
const SUPPORTED_IMAGES = [
  {
    type: "image/png",
    extension: ".png",
    matches: (value: Buffer) =>
      value.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex")),
  },
  {
    type: "image/jpeg",
    extension: ".jpg",
    matches: (value: Buffer) =>
      value.length >= 3 &&
      value[0] === 0xff &&
      value[1] === 0xd8 &&
      value[2] === 0xff,
  },
  {
    type: "image/gif",
    extension: ".gif",
    matches: (value: Buffer) =>
      value.subarray(0, 6).toString("ascii") === "GIF87a" ||
      value.subarray(0, 6).toString("ascii") === "GIF89a",
  },
  {
    type: "image/webp",
    extension: ".webp",
    matches: (value: Buffer) =>
      value.subarray(0, 4).toString("ascii") === "RIFF" &&
      value.subarray(8, 12).toString("ascii") === "WEBP",
  },
] as const;

export async function POST(request: NextRequest) {
  try {
    const imageLimit = getUploadLimits().image;
    const contentLength = Number(request.headers.get("content-length"));
    if (!Number.isSafeInteger(contentLength) || contentLength <= 0) {
      return NextResponse.json(
        { error: "Content-Length is required for image uploads." },
        { status: 411 }
      );
    }
    if (
      contentLength > imageLimit.bytes + MULTIPART_OVERHEAD_BYTES
    ) {
      return NextResponse.json(
        {
          error: `Image upload exceeds the ${formatUploadLimit(imageLimit.bytes)} limit.`,
        },
        { status: 413 }
      );
    }

    const formData = await request.formData();
    const file = formData.get("file");

    if (!(file instanceof File)) {
      return NextResponse.json(
        { error: "No file provided" },
        { status: 400 }
      );
    }
    if (file.size > imageLimit.bytes) {
      return NextResponse.json(
        {
          error: `Image upload exceeds the ${formatUploadLimit(imageLimit.bytes)} limit.`,
        },
        { status: 413 }
      );
    }

    const bytes = await file.arrayBuffer();
    const buffer = Buffer.from(bytes);
    const detected = SUPPORTED_IMAGES.find(
      ({ type, matches }) => type === file.type.toLowerCase() && matches(buffer)
    );
    if (!detected) {
      return NextResponse.json(
        {
          error:
            "Unsupported or invalid image. Use a valid PNG, JPEG, GIF, or WebP file.",
        },
        { status: 415 }
      );
    }

    const userDataDir = process.env.APP_DATA_DIRECTORY;
    if (!userDataDir) {
      return NextResponse.json(
        { error: "User data directory not found" },
        { status: 500 }
      );
    }
    // Create uploads directory if it doesn't exist
    const uploadsDir = path.join(userDataDir, "uploads");
    fs.mkdirSync(uploadsDir, { recursive: true });


    // Generate unique filename
    const filename = `${crypto.randomBytes(16).toString("hex")}${detected.extension}`;
    const filePath = path.join(uploadsDir, filename);

    // Write file to disk
    await fs.promises.writeFile(filePath, buffer, { flag: "wx" });

    // Return the relative path that can be used in the frontend
    return NextResponse.json({
      success: true,
      filePath: `${uploadsDir}/${filename}`
    });
  } catch (error) {
    console.error("Error saving image:", error);
    return NextResponse.json(
      { error: "Failed to save image" },
      { status: 500 }
    );
  }
}
