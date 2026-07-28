import { createHash } from "node:crypto";
import { realpath, readFile, stat } from "node:fs/promises";
import path from "node:path";

const MAX_EMBEDDED_FONT_SOURCE_BYTES = 32 * 1024 * 1024;
const DEFAULT_WINDOWS_FONT_ROOT = "C:\\Windows\\Fonts";

export type EmbeddedFontFaceKind =
  | "regular"
  | "bold"
  | "italic"
  | "boldItalic";
export type FontEmbeddingStrategy = "full" | "subset";

export interface SfntFontInspection {
  format: "ttf" | "otf";
  familyNames: readonly string[];
  fullNames: readonly string[];
  postscriptNames: readonly string[];
  tableTags: readonly string[];
  weightClass: number;
  fsType: number;
  variable: boolean;
}

export interface FontEmbeddingLicenseAssessment {
  allowed: boolean;
  decision:
    | "allowed-installable"
    | "allowed-editable"
    | "denied-restricted"
    | "denied-preview-print"
    | "denied-bitmap-only"
    | "denied-invalid";
  fsType: number;
  noSubsetting: boolean;
  strategyAllowed: boolean;
}

export interface ResolvedEmbeddedFontFile {
  sourceId: string;
  source: "server-font-allowlist" | "local-derived-static";
  /** Allowlist-relative name only; never exposes an arbitrary server path. */
  sourcePath: string;
  fileName: string;
  derivedFromVariable: boolean;
  sha256: string;
  sizeBytes: number;
  data: Buffer;
  inspection: SfntFontInspection;
  license: FontEmbeddingLicenseAssessment;
}

export interface ResolvedEmbeddedFontFace {
  family: string;
  face: EmbeddedFontFaceKind;
  weight: 400 | 700;
  style: "normal" | "italic";
  sourceId: string;
  variationCoordinates?: Readonly<Record<string, number>>;
}

export interface FontEmbeddingPlanFailure {
  family: string;
  face?: EmbeddedFontFaceKind;
  reason:
    | "family-not-allowlisted"
    | "source-unavailable"
    | "invalid-font"
    | "family-mismatch"
    | "license-restricted"
    | "variable-font-unsupported";
  detail: string;
}

export interface FontEmbeddingPlanStatus {
  policy: "opt-in";
  requested: boolean;
  eligible: boolean;
  strategy: FontEmbeddingStrategy;
  sourceFiles: number;
  faces: number;
  reason: "not-requested" | "eligible" | "unsupported" | "failed";
  failures: readonly FontEmbeddingPlanFailure[];
}

export interface FontEmbeddingPlan {
  requested: boolean;
  strategy: FontEmbeddingStrategy;
  files: readonly ResolvedEmbeddedFontFile[];
  faces: readonly ResolvedEmbeddedFontFace[];
  eligibleFamilies: ReadonlySet<string>;
  cacheDiscriminator: string;
  status: FontEmbeddingPlanStatus;
}

export interface ResolveFontEmbeddingPlanOptions {
  requested?: boolean;
  families?: readonly string[];
  strategy?: FontEmbeddingStrategy;
  /**
   * Variable fonts are rejected by default. A packaging engine may opt in only
   * after it has explicitly verified its PowerPoint compatibility behavior.
   */
  allowVariableFonts?: boolean;
}

interface AllowlistedCandidate {
  fileName: string;
  variable: boolean;
  source: ResolvedEmbeddedFontFile["source"];
  derivedFromVariable: boolean;
  faces: readonly Omit<ResolvedEmbeddedFontFace, "family" | "sourceId">[];
}

const NOTO_SANS_KR_CANDIDATES: readonly AllowlistedCandidate[] = [
  {
    fileName: "NotoSansKR-Regular-derived-static.ttf",
    variable: false,
    source: "local-derived-static",
    derivedFromVariable: true,
    faces: [{ face: "regular", weight: 400, style: "normal" }],
  },
  {
    fileName: "NotoSansKR-Bold-derived-static.ttf",
    variable: false,
    source: "local-derived-static",
    derivedFromVariable: true,
    faces: [{ face: "bold", weight: 700, style: "normal" }],
  },
  {
    fileName: "NotoSansKR-Regular.ttf",
    variable: false,
    source: "server-font-allowlist",
    derivedFromVariable: false,
    faces: [{ face: "regular", weight: 400, style: "normal" }],
  },
  {
    fileName: "NotoSansKR-Bold.ttf",
    variable: false,
    source: "server-font-allowlist",
    derivedFromVariable: false,
    faces: [{ face: "bold", weight: 700, style: "normal" }],
  },
  {
    fileName: "NotoSansKR-VF.ttf",
    variable: true,
    source: "server-font-allowlist",
    derivedFromVariable: false,
    faces: [
      {
        face: "regular",
        weight: 400,
        style: "normal",
        variationCoordinates: { wght: 400 },
      },
      {
        face: "bold",
        weight: 700,
        style: "normal",
        variationCoordinates: { wght: 700 },
      },
    ],
  },
];

const ALLOWLIST = new Map<string, readonly AllowlistedCandidate[]>([
  ["noto sans kr", NOTO_SANS_KR_CANDIDATES],
]);

function normalizeFamily(family: string): string {
  return family.trim().replace(/^['"]|['"]$/g, "");
}

function fontRoots(): string[] {
  const configured = process.env.PRESENTON_FONT_EMBEDDING_DIR?.trim();
  const windowsRoot = process.env.WINDIR?.trim();
  const roots = [
    configured && path.isAbsolute(configured) ? configured : undefined,
    windowsRoot
      ? path.join(windowsRoot, "Fonts")
      : DEFAULT_WINDOWS_FONT_ROOT,
  ].filter((value): value is string => Boolean(value));
  return [...new Set(roots.map((root) => path.resolve(root)))];
}

function u16(buffer: Buffer, offset: number): number {
  if (offset < 0 || offset + 2 > buffer.length) {
    throw new Error("truncated SFNT uint16");
  }
  return buffer.readUInt16BE(offset);
}

function u32(buffer: Buffer, offset: number): number {
  if (offset < 0 || offset + 4 > buffer.length) {
    throw new Error("truncated SFNT uint32");
  }
  return buffer.readUInt32BE(offset);
}

function decodeUtf16Be(value: Buffer): string {
  if (value.length % 2 !== 0) return "";
  const swapped = Buffer.allocUnsafe(value.length);
  for (let index = 0; index < value.length; index += 2) {
    swapped[index] = value[index + 1];
    swapped[index + 1] = value[index];
  }
  return swapped.toString("utf16le").replace(/\u0000/g, "").trim();
}

function readNameTable(
  buffer: Buffer,
  tableOffset: number,
  tableLength: number
): Pick<
  SfntFontInspection,
  "familyNames" | "fullNames" | "postscriptNames"
> {
  if (tableLength < 6 || tableOffset + tableLength > buffer.length) {
    throw new Error("invalid name table bounds");
  }
  const count = u16(buffer, tableOffset + 2);
  const stringsOffset = u16(buffer, tableOffset + 4);
  const recordsEnd = tableOffset + 6 + count * 12;
  const stringsStart = tableOffset + stringsOffset;
  if (recordsEnd > tableOffset + tableLength || stringsStart > tableOffset + tableLength) {
    throw new Error("invalid name table records");
  }

  const names = new Map<number, Set<string>>([
    [1, new Set()],
    [4, new Set()],
    [6, new Set()],
    [16, new Set()],
  ]);
  for (let index = 0; index < count; index += 1) {
    const recordOffset = tableOffset + 6 + index * 12;
    const platformId = u16(buffer, recordOffset);
    const nameId = u16(buffer, recordOffset + 6);
    const length = u16(buffer, recordOffset + 8);
    const offset = u16(buffer, recordOffset + 10);
    if (!names.has(nameId)) continue;
    const start = stringsStart + offset;
    const end = start + length;
    if (start < stringsStart || end > tableOffset + tableLength) continue;
    const raw = buffer.subarray(start, end);
    const decoded =
      platformId === 0 || platformId === 3
        ? decodeUtf16Be(raw)
        : raw.toString("latin1").replace(/\u0000/g, "").trim();
    if (decoded) names.get(nameId)?.add(decoded);
  }
  const familyNames = new Set([
    ...(names.get(1) ?? []),
    ...(names.get(16) ?? []),
  ]);
  return {
    familyNames: [...familyNames].sort(),
    fullNames: [...(names.get(4) ?? [])].sort(),
    postscriptNames: [...(names.get(6) ?? [])].sort(),
  };
}

export function inspectSfntFont(buffer: Buffer): SfntFontInspection {
  if (buffer.length < 12) throw new Error("font source is too small");
  const signature = buffer.toString("ascii", 0, 4);
  const scalarType = u32(buffer, 0);
  const format =
    scalarType === 0x00010000 || signature === "true"
      ? "ttf"
      : signature === "OTTO"
        ? "otf"
        : undefined;
  if (!format) throw new Error("unsupported SFNT signature");

  const numTables = u16(buffer, 4);
  if (numTables < 1 || 12 + numTables * 16 > buffer.length) {
    throw new Error("invalid SFNT table directory");
  }
  const tables = new Map<string, { offset: number; length: number }>();
  for (let index = 0; index < numTables; index += 1) {
    const recordOffset = 12 + index * 16;
    const tag = buffer.toString("ascii", recordOffset, recordOffset + 4);
    const offset = u32(buffer, recordOffset + 8);
    const length = u32(buffer, recordOffset + 12);
    if (offset > buffer.length || length > buffer.length - offset) {
      throw new Error(`invalid SFNT table bounds: ${tag}`);
    }
    tables.set(tag, { offset, length });
  }
  const os2 = tables.get("OS/2");
  const name = tables.get("name");
  if (!os2 || os2.length < 10) throw new Error("font is missing a valid OS/2 table");
  if (!name) throw new Error("font is missing a name table");
  const parsedNames = readNameTable(buffer, name.offset, name.length);
  return {
    format,
    ...parsedNames,
    tableTags: [...tables.keys()].sort(),
    weightClass: u16(buffer, os2.offset + 4),
    fsType: u16(buffer, os2.offset + 8),
    variable: tables.has("fvar") || tables.has("gvar"),
  };
}

export function assessEmbeddingLicense(
  fsType: number,
  strategy: FontEmbeddingStrategy
): FontEmbeddingLicenseAssessment {
  const normalized = fsType & 0xffff;
  const restricted = (normalized & 0x0002) !== 0;
  const previewPrint = (normalized & 0x0004) !== 0;
  const editable = (normalized & 0x0008) !== 0;
  const bitmapOnly = (normalized & 0x0200) !== 0;
  const noSubsetting = (normalized & 0x0100) !== 0;
  const mutuallyExclusiveBits = Number(restricted) + Number(previewPrint) + Number(editable);
  let decision: FontEmbeddingLicenseAssessment["decision"];
  if (mutuallyExclusiveBits > 1) decision = "denied-invalid";
  else if (restricted) decision = "denied-restricted";
  else if (previewPrint) decision = "denied-preview-print";
  else if (bitmapOnly) decision = "denied-bitmap-only";
  else if (editable) decision = "allowed-editable";
  else decision = "allowed-installable";
  const allowed = decision.startsWith("allowed-");
  return {
    allowed,
    decision,
    fsType: normalized,
    noSubsetting,
    strategyAllowed: allowed && !(strategy === "subset" && noSubsetting),
  };
}

async function loadAllowlistedCandidate(
  family: string,
  candidate: AllowlistedCandidate,
  strategy: FontEmbeddingStrategy
): Promise<ResolvedEmbeddedFontFile | undefined> {
  for (const root of fontRoots()) {
    const expectedPath = path.resolve(root, candidate.fileName);
    if (path.dirname(expectedPath).toLowerCase() !== root.toLowerCase()) continue;
    try {
      const [resolvedRoot, resolvedFile] = await Promise.all([
        realpath(root),
        realpath(expectedPath),
      ]);
      if (path.dirname(resolvedFile).toLowerCase() !== resolvedRoot.toLowerCase()) {
        continue;
      }
      const fileStat = await stat(resolvedFile);
      if (
        !fileStat.isFile() ||
        fileStat.size < 12 ||
        fileStat.size > MAX_EMBEDDED_FONT_SOURCE_BYTES
      ) {
        continue;
      }
      const data = await readFile(resolvedFile);
      const inspection = inspectSfntFont(data);
      const expectedFamily = family.toLowerCase();
      if (
        !inspection.familyNames.some(
          (name) => normalizeFamily(name).toLowerCase() === expectedFamily
        )
      ) {
        throw new Error(`font family does not match allowlist entry ${family}`);
      }
      if (inspection.variable !== candidate.variable) {
        throw new Error(
          candidate.variable
            ? "allowlisted variable font is missing fvar/gvar"
            : "allowlisted static font unexpectedly contains variation tables"
        );
      }
      const license = assessEmbeddingLicense(inspection.fsType, strategy);
      return {
        sourceId: `${candidate.source}:${candidate.fileName}`,
        source: candidate.source,
        sourcePath: candidate.fileName,
        fileName: candidate.fileName,
        derivedFromVariable: candidate.derivedFromVariable,
        sha256: createHash("sha256").update(data).digest("hex"),
        sizeBytes: data.length,
        data,
        inspection,
        license,
      };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
      throw error;
    }
  }
  return undefined;
}

function buildCacheDiscriminator(
  requested: boolean,
  strategy: FontEmbeddingStrategy,
  files: readonly ResolvedEmbeddedFontFile[]
): string {
  if (!requested) return "font-embedding=off";
  const sources = files
    .map((file) => `${file.sourceId}:${file.sha256}`)
    .sort()
    .join(",");
  return `font-embedding=${strategy}:${sources || "unavailable"}`;
}

export async function resolveFontEmbeddingPlan(
  options: ResolveFontEmbeddingPlanOptions = {}
): Promise<FontEmbeddingPlan> {
  const requested = options.requested === true;
  const strategy = options.strategy ?? "full";
  if (!requested) {
    return {
      requested: false,
      strategy,
      files: [],
      faces: [],
      eligibleFamilies: new Set(),
      cacheDiscriminator: buildCacheDiscriminator(false, strategy, []),
      status: {
        policy: "opt-in",
        requested: false,
        eligible: false,
        strategy,
        sourceFiles: 0,
        faces: 0,
        reason: "not-requested",
        failures: [],
      },
    };
  }

  const requestedFamilies = [
    ...new Set(
      (options.families ?? ["Noto Sans KR"])
        .map(normalizeFamily)
        .filter(Boolean)
    ),
  ];
  const files: ResolvedEmbeddedFontFile[] = [];
  const faces: ResolvedEmbeddedFontFace[] = [];
  const failures: FontEmbeddingPlanFailure[] = [];
  const eligibleFamilies = new Set<string>();

  for (const family of requestedFamilies) {
    const candidates = ALLOWLIST.get(family.toLowerCase());
    if (!candidates) {
      failures.push({
        family,
        reason: "family-not-allowlisted",
        detail: "The family is not present in the server font allowlist.",
      });
      continue;
    }
    let familyResolved = false;
    const resolvedFaceKinds = new Set<EmbeddedFontFaceKind>();
    for (const candidate of candidates) {
      if (
        resolvedFaceKinds.has("regular") &&
        resolvedFaceKinds.has("bold")
      ) {
        break;
      }
      let file: ResolvedEmbeddedFontFile | undefined;
      try {
        file = await loadAllowlistedCandidate(family, candidate, strategy);
      } catch (error) {
        failures.push({
          family,
          reason: String(error).includes("family does not match")
            ? "family-mismatch"
            : "invalid-font",
          detail: error instanceof Error ? error.message : String(error),
        });
        continue;
      }
      if (!file) continue;
      if (candidate.variable && options.allowVariableFonts !== true) {
        failures.push({
          family,
          reason: "variable-font-unsupported",
          detail:
            "Only a variable source was found; this packaging engine has not opted into variable-font compatibility.",
        });
        continue;
      }
      if (!file.license.allowed || !file.license.strategyAllowed) {
        failures.push({
          family,
          reason: "license-restricted",
          detail: file.license.strategyAllowed
            ? file.license.decision
            : `${file.license.decision}; fsType forbids subsetting`,
        });
        continue;
      }
      files.push(file);
      faces.push(
        ...candidate.faces
          .filter((face) => !resolvedFaceKinds.has(face.face))
          .map((face) => {
            resolvedFaceKinds.add(face.face);
            return {
              family,
              ...face,
              sourceId: file.sourceId,
            };
          })
      );
      eligibleFamilies.add(family);
      familyResolved = true;
      if (candidate.variable) break;
    }
    if (!familyResolved && !failures.some((failure) => failure.family === family)) {
      failures.push({
        family,
        reason: "source-unavailable",
        detail: "No allowlisted static or variable font source was found.",
      });
    }
  }

  const eligible = files.length > 0 && faces.length > 0;
  return {
    requested,
    strategy,
    files,
    faces,
    eligibleFamilies,
    cacheDiscriminator: buildCacheDiscriminator(requested, strategy, files),
    status: {
      policy: "opt-in",
      requested,
      eligible,
      strategy,
      sourceFiles: files.length,
      faces: faces.length,
      reason: eligible ? "eligible" : failures.length > 0 ? "unsupported" : "failed",
      failures,
    },
  };
}
