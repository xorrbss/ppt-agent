"use client";

import { useEffect, useState } from "react";

import {
  DEFAULT_DOCUMENT_UPLOAD_MIB,
  DEFAULT_IMAGE_UPLOAD_MIB,
} from "./upload-limits";

type Limit = { bytes: number; mb: number; hardMaxMb: number };

export interface UploadLimitsResponse {
  document: Limit;
  image: Limit;
  requestTotal: Limit;
  reason: string;
}

const MIB = 1024 * 1024;
const DEFAULT_LIMITS: UploadLimitsResponse = {
  document: {
    bytes: DEFAULT_DOCUMENT_UPLOAD_MIB * MIB,
    mb: DEFAULT_DOCUMENT_UPLOAD_MIB,
    hardMaxMb: 512,
  },
  image: {
    bytes: DEFAULT_IMAGE_UPLOAD_MIB * MIB,
    mb: DEFAULT_IMAGE_UPLOAD_MIB,
    hardMaxMb: 64,
  },
  requestTotal: { bytes: 512 * MIB, mb: 512, hardMaxMb: 512 },
  reason:
    "Limits bound request memory, temporary disk use, conversion time, and denial-of-service exposure.",
};

export function useUploadLimits(): UploadLimitsResponse {
  const [limits, setLimits] = useState(DEFAULT_LIMITS);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/upload-limits", {
      cache: "no-store",
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("upload_limit_lookup_failed");
        return response.json() as Promise<UploadLimitsResponse>;
      })
      .then(setLimits)
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          console.warn("Using default upload limits:", error);
        }
      });
    return () => controller.abort();
  }, []);

  return limits;
}
