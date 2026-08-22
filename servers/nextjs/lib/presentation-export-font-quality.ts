export interface PresentationExportFontEmbeddingStatus {
  policy: "opt-in";
  requested: boolean;
  applied: boolean;
  embeddedFontFiles: number;
  embeddedTypefaces?: number;
  strategy?: "full" | "subset";
  editLimitation?: "none" | "characters-outside-subset-may-substitute";
  faces?: PresentationExportEmbeddedFontFaceStatus[];
  failures?: PresentationExportFontEmbeddingFailure[];
  reason: "not-requested" | "embedded" | "unsupported" | "failed";
  nativeTextUsage?:
    | "used"
    | "not-used"
    | "raster-only-unused"
    | "not-embedded"
    | "unverified";
  nativeTextElements?: number;
  nativeTextElementsUsingEmbeddedTypefaces?: number;
  nativeTypefaceFamilies?: string[];
}

export interface PresentationExportEmbeddedFontFaceStatus {
  typeface: string;
  face: "regular" | "bold" | "italic" | "boldItalic";
  weight: number;
  style: "normal" | "italic";
  source: string;
  sourcePath?: string;
  sourceSha256: string;
  sourceBytes: number;
  embeddedBytes: number;
  fsType: number;
  licenseDecision:
    | "allowed-installable"
    | "allowed-editable"
    | "denied-restricted"
    | "denied-preview-print"
    | "denied-bitmap-only"
    | "denied-invalid";
  subset: boolean;
  strategy: "full" | "subset";
  partName: string;
  format: string;
  derivedFromVariable?: boolean;
}

export interface PresentationExportFontEmbeddingFailure {
  family?: string;
  face?: "regular" | "bold" | "italic" | "boldItalic";
  reason: string;
  detail?: string;
}

export interface PresentationExportFontRenderingQuality {
  browserFontFilesCollected: number;
  browserCollectionFailures: number;
  powerpointTypefacePolicy: "central-compatible-fallbacks";
}
