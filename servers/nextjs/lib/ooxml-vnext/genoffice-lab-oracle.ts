import { createHash } from "node:crypto";

import {
  evaluateGenOfficeCompliance,
  GENOFFICE_LAB_ORACLE_CAPABILITIES,
  GENOFFICE_LAB_ORACLE_POLICY,
  type GenOfficeComplianceDecisionDto,
  type GenOfficeComplianceEvidenceDto,
  type GenOfficeLabCapabilityDto,
} from "./compliance.ts";

interface PrivateGenOfficeModule {
  readonly openPptx: (input: Uint8Array) => Promise<unknown>;
  readonly savePptx: (opened: unknown) => Promise<unknown>;
}

export type GenOfficeLabOracleErrorCode =
  | "COMPLIANCE_REJECTED"
  | "ORACLE_MODULE_UNAVAILABLE"
  | "ORACLE_MODULE_CONTRACT_MISMATCH"
  | "BUFFERED_SAVE_INVALID_RESULT";

export class GenOfficeLabOracleError extends Error {
  readonly code: GenOfficeLabOracleErrorCode;

  readonly decision?: GenOfficeComplianceDecisionDto;

  constructor(
    code: GenOfficeLabOracleErrorCode,
    message: string,
    decision?: GenOfficeComplianceDecisionDto,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "GenOfficeLabOracleError";
    this.code = code;
    this.decision = decision;
  }
}

export interface BufferedNoopOracleReceiptDto {
  readonly schemaVersion: 1;
  readonly operation: "BUFFERED_NOOP_OPEN_SAVE";
  readonly engineCommit: string;
  readonly inputSha256: string;
  readonly inputByteLength: number;
  readonly outputSha256: string;
  readonly outputByteLength: number;
  readonly streamingUsed: false;
  readonly mutationRequested: false;
}

export interface BufferedNoopOracleResult {
  /** Detached copy suitable for the caller's independent package/closure audit. */
  readonly outputBytes: Uint8Array;
  readonly receipt: BufferedNoopOracleReceiptDto;
}

export interface GenOfficeBufferedNoopLabOracle {
  readonly capabilities: GenOfficeLabCapabilityDto;
  readonly compliance: GenOfficeComplianceDecisionDto;
  runBufferedNoop(inputBytes: Uint8Array): Promise<BufferedNoopOracleResult>;
}

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function requirePrivateModule(value: unknown): PrivateGenOfficeModule {
  if (!isRecord(value)) {
    throw new GenOfficeLabOracleError(
      "ORACLE_MODULE_CONTRACT_MISMATCH",
      "The exact-pinned lab module did not return a module namespace.",
    );
  }
  const openPptx = value.openPptx;
  const savePptx = value.savePptx;
  if (typeof openPptx !== "function" || typeof savePptx !== "function") {
    throw new GenOfficeLabOracleError(
      "ORACLE_MODULE_CONTRACT_MISMATCH",
      "The lab module must expose only the openPptx/savePptx entry points needed by buffered no-op.",
    );
  }
  return {
    openPptx: openPptx as PrivateGenOfficeModule["openPptx"],
    savePptx: savePptx as PrivateGenOfficeModule["savePptx"],
  };
}

function toDetachedBytes(value: unknown): Uint8Array {
  if (value instanceof Uint8Array) return Uint8Array.from(value);
  if (value instanceof ArrayBuffer) return new Uint8Array(value.slice(0));
  throw new GenOfficeLabOracleError(
    "BUFFERED_SAVE_INVALID_RESULT",
    "The buffered save result must be a non-empty Uint8Array or ArrayBuffer.",
  );
}

/**
 * Load the optional engine only after its pure DTO evidence passes the exact-pin
 * gate. The module namespace and opened mutable deck remain private to this
 * closure and can never cross the public adapter boundary.
 */
export async function loadGenOfficeBufferedNoopLabOracle(
  evidence: GenOfficeComplianceEvidenceDto,
): Promise<GenOfficeBufferedNoopLabOracle> {
  const compliance = evaluateGenOfficeCompliance(evidence);
  if (!compliance.accepted) {
    throw new GenOfficeLabOracleError(
      "COMPLIANCE_REJECTED",
      "GenOffice lab oracle compliance evidence was rejected.",
      compliance,
    );
  }

  // Keep the specifier data-driven for optional installation, but it has already
  // been compared byte-for-byte with the single policy allowlist entry above.
  const specifier = evidence.boundary.dynamicImportSpecifier;
  let namespace: unknown;
  try {
    namespace = await import(specifier);
  } catch (cause) {
    throw new GenOfficeLabOracleError(
      "ORACLE_MODULE_UNAVAILABLE",
      `The approved optional lab module is unavailable: ${GENOFFICE_LAB_ORACLE_POLICY.dynamicImportSpecifier}`,
      compliance,
      { cause },
    );
  }
  const engine = requirePrivateModule(namespace);

  return Object.freeze({
    capabilities: GENOFFICE_LAB_ORACLE_CAPABILITIES,
    compliance,
    async runBufferedNoop(
      inputBytes: Uint8Array,
    ): Promise<BufferedNoopOracleResult> {
      if (!(inputBytes instanceof Uint8Array) || inputBytes.byteLength === 0) {
        throw new TypeError("inputBytes must be a non-empty Uint8Array.");
      }
      const isolatedInput = Uint8Array.from(inputBytes);
      const opened = await engine.openPptx(isolatedInput);
      // savePptx is the buffered API. There is deliberately no streaming option,
      // text/master/layout mutation entry point, or generic engine escape hatch.
      const outputBytes = toDetachedBytes(await engine.savePptx(opened));
      if (outputBytes.byteLength === 0) {
        throw new GenOfficeLabOracleError(
          "BUFFERED_SAVE_INVALID_RESULT",
          "The buffered save result must not be empty.",
        );
      }
      const receipt = Object.freeze({
        schemaVersion: 1 as const,
        operation: "BUFFERED_NOOP_OPEN_SAVE" as const,
        engineCommit: GENOFFICE_LAB_ORACLE_POLICY.commit,
        inputSha256: sha256(isolatedInput),
        inputByteLength: isolatedInput.byteLength,
        outputSha256: sha256(outputBytes),
        outputByteLength: outputBytes.byteLength,
        streamingUsed: false as const,
        mutationRequested: false as const,
      });
      return Object.freeze({ outputBytes, receipt });
    },
  });
}
