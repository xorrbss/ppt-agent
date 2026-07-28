const MIB = 1024 * 1024;

export const DEFAULT_DOCUMENT_UPLOAD_MIB = 100;
export const DEFAULT_IMAGE_UPLOAD_MIB = 20;
export const DEFAULT_REQUEST_TOTAL_MIB = 512;
export const HARD_DOCUMENT_UPLOAD_MIB = 512;
export const HARD_IMAGE_UPLOAD_MIB = 64;
export const HARD_REQUEST_TOTAL_MIB = 512;

function configuredMib(
  name: string,
  fallbackMib: number,
  hardMaxMib: number,
  env: Readonly<Record<string, string | undefined>> = process.env
): number {
  const raw = env[name]?.trim();
  if (!raw) return fallbackMib;
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) return fallbackMib;
  return Math.min(parsed, hardMaxMib);
}

export function getUploadLimits(
  env: Readonly<Record<string, string | undefined>> = process.env
) {
  const documentMib = configuredMib(
    "PRESENTON_MAX_UPLOAD_MB",
    DEFAULT_DOCUMENT_UPLOAD_MIB,
    HARD_DOCUMENT_UPLOAD_MIB,
    env
  );
  const requestedTotalMib = configuredMib(
    "PRESENTON_MAX_UPLOAD_TOTAL_MB",
    DEFAULT_REQUEST_TOTAL_MIB,
    HARD_REQUEST_TOTAL_MIB,
    env
  );
  const imageMib = configuredMib(
    "PRESENTON_MAX_IMAGE_UPLOAD_MB",
    DEFAULT_IMAGE_UPLOAD_MIB,
    HARD_IMAGE_UPLOAD_MIB,
    env
  );
  return {
    document: {
      bytes: documentMib * MIB,
      mb: documentMib,
      hardMaxMb: HARD_DOCUMENT_UPLOAD_MIB,
    },
    image: {
      bytes: imageMib * MIB,
      mb: imageMib,
      hardMaxMb: HARD_IMAGE_UPLOAD_MIB,
    },
    requestTotal: {
      bytes: Math.max(requestedTotalMib, documentMib) * MIB,
      mb: Math.max(requestedTotalMib, documentMib),
      hardMaxMb: HARD_REQUEST_TOTAL_MIB,
    },
    reason:
      "Limits bound request memory, temporary disk use, conversion time, and denial-of-service exposure.",
  };
}

export function formatUploadLimit(bytes: number): string {
  return `${Math.floor(bytes / MIB)} MB`;
}
