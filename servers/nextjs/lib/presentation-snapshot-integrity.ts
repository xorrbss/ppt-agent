import { createHash } from "node:crypto";

const SHA256_HEX_PATTERN = /^[0-9a-f]{64}$/i;

export class PresentationSnapshotMismatchError extends Error {
  readonly code = "presentation_snapshot_mismatch";

  constructor() {
    super("Presentation changed while the export snapshot was being rendered.");
    this.name = "PresentationSnapshotMismatchError";
  }
}

export function normalizeExpectedPresentationSha256(
  value: string | undefined
): string | undefined {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return undefined;
  if (!SHA256_HEX_PATTERN.test(normalized)) {
    throw new Error("Expected presentation SHA-256 must be 64 hexadecimal characters.");
  }
  return normalized;
}

export function assertPresentationSnapshotIntegrity(
  body: string,
  expectedSha256: string | undefined
): void {
  const expected = normalizeExpectedPresentationSha256(expectedSha256);
  if (!expected) return;

  const actual = createHash("sha256").update(body).digest("hex");
  if (actual !== expected) {
    throw new PresentationSnapshotMismatchError();
  }
}
