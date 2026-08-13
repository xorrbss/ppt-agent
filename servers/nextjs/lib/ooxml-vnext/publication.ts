import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

const SHA256 = /^[a-f0-9]{64}$/i;
const OPTIONAL_FSYNC_ERROR_CODES = new Set([
  "EACCES",
  "EINVAL",
  "EISDIR",
  "ENOSYS",
  "ENOTSUP",
  "EPERM",
]);

export interface BufferedPublicationCandidate {
  /** Absolute same-directory temporary path that the callback must reopen. */
  path: string;
  byteLength: number;
  expectedSha256: string;
}

export interface BufferedPublicationRequest {
  destinationPath: string;
  /** Fully buffered artifact bytes. Streams are intentionally not accepted. */
  bytes: Uint8Array;
  /** Throw unless the temporary artifact validates and reopens successfully. */
  validateAndReopen: (candidate: BufferedPublicationCandidate) => Promise<void>;
  /**
   * Optional compare-before-publication fence. `null` requires an absent
   * destination; a digest requires an existing destination with that digest.
   * Undefined is an explicit unconditional atomic replacement.
   */
  expectedDestinationSha256?: string | null;
}

export interface BufferedPublicationReceipt {
  schemaVersion: 1;
  operation: "BUFFERED_ATOMIC_PUBLICATION";
  destinationPath: string;
  sha256: string;
  byteLength: number;
  validation: "reopened";
  candidateDigestVerified: true;
  atomicRename: true;
  replacedExisting: boolean;
  fileFsync: "performed" | "unsupported";
  directoryFsync: "performed" | "unsupported";
  publishedReadBackSha256: string;
}

interface DestinationState {
  exists: boolean;
  sha256?: string;
}

function publicationError(reason: string): Error {
  return new Error(`ooxml-publication-rejected:${reason}`);
}

function sha256(value: Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function errorCode(error: unknown): string | undefined {
  return (error as NodeJS.ErrnoException | undefined)?.code;
}

async function destinationState(destinationPath: string): Promise<DestinationState> {
  try {
    const stat = await fs.lstat(destinationPath);
    if (!stat.isFile()) throw publicationError("destination-is-not-a-regular-file");
    if (stat.isSymbolicLink()) throw publicationError("destination-is-a-symbolic-link");
    return { exists: true, sha256: sha256(await fs.readFile(destinationPath)) };
  } catch (error) {
    if (errorCode(error) === "ENOENT") return { exists: false };
    throw error;
  }
}

function assertDestinationPrecondition(
  state: DestinationState,
  expected: string | null | undefined
): void {
  if (expected === undefined) return;
  if (expected === null) {
    if (state.exists) throw publicationError("destination-was-expected-to-be-absent");
    return;
  }
  if (!SHA256.test(expected)) {
    throw publicationError("expected-destination-sha256-is-invalid");
  }
  if (!state.exists) throw publicationError("expected-destination-is-missing");
  if (state.sha256 !== expected.toLowerCase()) {
    throw publicationError("expected-destination-sha256-mismatch");
  }
}

async function syncFile(handle: fs.FileHandle): Promise<"performed" | "unsupported"> {
  try {
    await handle.sync();
    return "performed";
  } catch (error) {
    if (OPTIONAL_FSYNC_ERROR_CODES.has(errorCode(error) ?? "")) return "unsupported";
    throw error;
  }
}

async function syncDirectory(directory: string): Promise<"performed" | "unsupported"> {
  let handle: fs.FileHandle | undefined;
  try {
    handle = await fs.open(directory, "r");
    return await syncFile(handle);
  } catch (error) {
    if (OPTIONAL_FSYNC_ERROR_CODES.has(errorCode(error) ?? "")) return "unsupported";
    throw error;
  } finally {
    await handle?.close().catch(() => {});
  }
}

function assertRequest(request: BufferedPublicationRequest): string {
  if (!path.isAbsolute(request.destinationPath)) {
    throw publicationError("destination-path-must-be-absolute");
  }
  if (!(request.bytes instanceof Uint8Array)) {
    throw publicationError("artifact-must-be-buffered-bytes");
  }
  if (typeof request.validateAndReopen !== "function") {
    throw publicationError("validate-and-reopen-callback-is-required");
  }
  const destinationPath = path.resolve(request.destinationPath);
  if (path.basename(destinationPath).length === 0) {
    throw publicationError("destination-path-must-name-a-file");
  }
  return destinationPath;
}

/**
 * Durably publish fully buffered bytes without exposing a streaming save path.
 *
 * The candidate is written beside its destination, reopened by the caller,
 * re-read and digested, fsynced when supported, and only then atomically
 * renamed. Replacement never falls back to unlink-then-rename, which would
 * create a destructive visibility gap on Windows.
 */
export async function publishBufferedArtifact(
  request: BufferedPublicationRequest
): Promise<BufferedPublicationReceipt> {
  const destinationPath = assertRequest(request);
  const directory = path.dirname(destinationPath);
  const directoryStat = await fs.stat(directory).catch((error) => {
    if (errorCode(error) === "ENOENT") throw publicationError("destination-directory-is-missing");
    throw error;
  });
  if (!directoryStat.isDirectory()) throw publicationError("destination-parent-is-not-a-directory");

  const bufferedBytes = Uint8Array.from(request.bytes);
  const expectedCandidateSha256 = sha256(bufferedBytes);
  const initialDestination = await destinationState(destinationPath);
  assertDestinationPrecondition(initialDestination, request.expectedDestinationSha256);

  // Keep the destination extension so PowerPoint/LibreOffice reopen callbacks
  // do not have to guess the candidate's package type from a generic `.tmp`.
  const extension = path.extname(destinationPath).slice(0, 16) || ".tmp";
  const safeStem = path.basename(destinationPath, path.extname(destinationPath)).slice(0, 64);
  const temporaryPath = path.join(
    directory,
    `.${safeStem}.${randomUUID()}.candidate${extension}`
  );
  let handle: fs.FileHandle | undefined;
  let renamed = false;
  try {
    handle = await fs.open(temporaryPath, "wx", 0o600);
    await handle.writeFile(bufferedBytes);

    await request.validateAndReopen({
      path: temporaryPath,
      byteLength: bufferedBytes.byteLength,
      expectedSha256: expectedCandidateSha256,
    });

    // Validation callbacks are not trusted to preserve the candidate. Re-read
    // after validation and fail closed if any byte changed.
    const validatedBytes = await fs.readFile(temporaryPath);
    const validatedSha256 = sha256(validatedBytes);
    if (
      validatedBytes.byteLength !== bufferedBytes.byteLength ||
      validatedSha256 !== expectedCandidateSha256
    ) {
      throw publicationError("candidate-changed-during-validation");
    }

    const fileFsync = await syncFile(handle);
    await handle.close();
    handle = undefined;

    // Re-check the fence immediately before the atomic operation. There is no
    // unlink fallback: rename either replaces atomically or publication fails
    // while preserving the old destination.
    const finalDestination = await destinationState(destinationPath);
    assertDestinationPrecondition(finalDestination, request.expectedDestinationSha256);
    await fs.rename(temporaryPath, destinationPath);
    renamed = true;

    const directoryFsync = await syncDirectory(directory);
    const publishedBytes = await fs.readFile(destinationPath);
    const publishedReadBackSha256 = sha256(publishedBytes);
    if (
      publishedBytes.byteLength !== bufferedBytes.byteLength ||
      publishedReadBackSha256 !== expectedCandidateSha256
    ) {
      throw publicationError("published-read-back-digest-mismatch");
    }

    return {
      schemaVersion: 1,
      operation: "BUFFERED_ATOMIC_PUBLICATION",
      destinationPath,
      sha256: expectedCandidateSha256,
      byteLength: bufferedBytes.byteLength,
      validation: "reopened",
      candidateDigestVerified: true,
      atomicRename: true,
      replacedExisting: finalDestination.exists,
      fileFsync,
      directoryFsync,
      publishedReadBackSha256,
    };
  } finally {
    await handle?.close().catch(() => {});
    if (!renamed) await fs.unlink(temporaryPath).catch(() => {});
  }
}
