"use client";
/**
 * Checks whether this server currently supports source-content-preserving
 * SVG generation for the selected authored style, so the upload form can
 * disable/explain that generation-strategy option. Extracted from
 * UploadPage.tsx (file-size ratchet split); behaviour unchanged, only the
 * module boundary moved.
 */
import { useEffect, useState } from "react";

import AuthoredStylesApi from "../../../services/api/authored";

export const useSourceSvgCapability = (sourceContentMode: boolean, authoredStyle: string) => {
  const [sourceSvgCapability, setSourceSvgCapability] = useState<boolean | null>(null);

  useEffect(() => {
    if (!sourceContentMode) {
      setSourceSvgCapability(null);
      return;
    }
    let cancelled = false;
    setSourceSvgCapability(null);
    void AuthoredStylesApi.getDocumentCanvasCapabilities({
      generationStrategy: "svg",
      authoredStyle,
      sourceContentMode: true,
      // Company-standard masters are Authored-only and are omitted from this route.
      hasCompanyStandard: false,
    }).then((capability) => {
      if (!cancelled) setSourceSvgCapability(capability.sourceContentSvg.enabled);
    });
    return () => {
      cancelled = true;
    };
  }, [authoredStyle, sourceContentMode]);

  return sourceSvgCapability;
};
