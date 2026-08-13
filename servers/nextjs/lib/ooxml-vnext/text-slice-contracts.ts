export type StableTextSelector =
  | {
      kind: "placeholder";
      idx: number;
      role?: string;
      occurrence?: number;
    }
  | {
      kind: "element";
      id: number | string;
      occurrence?: number;
    };

export interface ExpectedOldText {
  text: string;
  sha256?: string;
}

export interface TextSlicePatchRequest {
  partName: string;
  selector: StableTextSelector;
  expected: ExpectedOldText;
  replacement: string;
}

export interface TextSlicePatchReceipt {
  schemaVersion: 1;
  operation: "PATCH_TEXT_SLICE";
  partName: string;
  selector: StableTextSelector;
  sourceSha256: string;
  resultSha256: string;
  oldTextSha256: string;
  replacementTextSha256: string;
  sourceSliceSha256: string;
  resultSliceSha256: string;
  sourceSlice: { startByte: number; endByte: number };
  resultSlice: { startByte: number; endByte: number };
  outsideSliceDiffBytes: 0;
  outsideSliceUnchanged: true;
}

export interface TextSlicePatchResult {
  bytes: Uint8Array;
  receipt: TextSlicePatchReceipt;
}
