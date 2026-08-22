import { createHash } from "node:crypto";
import { realpath, readFile, stat } from "node:fs/promises";
import path from "node:path";

import { resolveAppDataDirectory } from "../app-data-directory.ts";

import {
  type EmbeddedFontFaceKind,
  type FontEmbeddingLicenseAssessment,
  type FontEmbeddingStrategy,
  type SfntFontInspection,
  assessEmbeddingLicense,
  inspectSfntFont,
} from "./font-sfnt-inspection.ts";

export type {
  EmbeddedFontFaceKind,
  FontEmbeddingLicenseAssessment,
  FontEmbeddingStrategy,
  SfntFontInspection,
};
export { assessEmbeddingLicense, inspectSfntFont };

const MAX_EMBEDDED_FONT_SOURCE_BYTES = 32 * 1024 * 1024;
const DEFAULT_WINDOWS_FONT_ROOT = "C:\\Windows\\Fonts";

export interface ResolvedEmbeddedFontFile {
  sourceId: string;
  source: "server-font-allowlist" | "local-derived-static" | "windows-system-reviewed";
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
    | "license-unreviewed"
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

const IBM_PLEX_MONO_CANDIDATES: readonly AllowlistedCandidate[] = [
  {
    fileName: "IBMPlexMono-Regular.ttf",
    variable: false,
    source: "server-font-allowlist",
    derivedFromVariable: false,
    faces: [{ face: "regular", weight: 400, style: "normal" }],
  },
];

const MALGUN_GOTHIC_CANDIDATES: readonly AllowlistedCandidate[] = [
  {
    fileName: "malgun.ttf",
    variable: false,
    source: "windows-system-reviewed",
    derivedFromVariable: false,
    faces: [{ face: "regular", weight: 400, style: "normal" }],
  },
  {
    fileName: "malgunbd.ttf",
    variable: false,
    source: "windows-system-reviewed",
    derivedFromVariable: false,
    faces: [{ face: "bold", weight: 700, style: "normal" }],
  },
];

const BATANG_COLLECTION_CANDIDATES: readonly AllowlistedCandidate[] = [
  {
    // TTC remains deliberately fail-closed until the OOXML packager can bind
    // an explicit collection index. The attempt is retained as an invalid-font
    // receipt instead of silently substituting a different serif face.
    fileName: "batang.ttc",
    variable: false,
    source: "windows-system-reviewed",
    derivedFromVariable: false,
    faces: [{ face: "regular", weight: 400, style: "normal" }],
  },
];

const CONSOLAS_CANDIDATES: readonly AllowlistedCandidate[] = [
  {
    fileName: "consola.ttf",
    variable: false,
    source: "windows-system-reviewed",
    derivedFromVariable: false,
    faces: [{ face: "regular", weight: 400, style: "normal" }],
  },
  {
    fileName: "consolab.ttf",
    variable: false,
    source: "windows-system-reviewed",
    derivedFromVariable: false,
    faces: [{ face: "bold", weight: 700, style: "normal" }],
  },
];

const ALLOWLIST = new Map<string, readonly AllowlistedCandidate[]>([
  ["noto sans kr", NOTO_SANS_KR_CANDIDATES],
  ["ibm plex mono", IBM_PLEX_MONO_CANDIDATES],
  ["malgun gothic", MALGUN_GOTHIC_CANDIDATES],
  ["batang", BATANG_COLLECTION_CANDIDATES],
  ["batangche", BATANG_COLLECTION_CANDIDATES],
  ["consolas", CONSOLAS_CANDIDATES],
]);

function normalizeFamily(family: string): string {
  return family.trim().replace(/^['"]|['"]$/g, "");
}

function fontRoots(): string[] {
  const configured = process.env.PRESENTON_FONT_EMBEDDING_DIR?.trim();
  const windowsRoot = process.env.WINDIR?.trim();
  let appDataAllowlist: string | undefined;
  try {
    appDataAllowlist = path.join(
      resolveAppDataDirectory(),
      "font-embedding-allowlist"
    );
  } catch {
    // Unit tests and isolated policy callers may not configure app-data.
  }
  const roots = [
    configured && path.isAbsolute(configured) ? configured : undefined,
    appDataAllowlist,
    windowsRoot
      ? path.join(windowsRoot, "Fonts")
      : DEFAULT_WINDOWS_FONT_ROOT,
  ].filter((value): value is string => Boolean(value));
  return [...new Set(roots.map((root) => path.resolve(root)))];
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
      if (
        candidate.source === "windows-system-reviewed" &&
        !inspection.licenseDescriptions.some((description) => {
          const normalized = description.toLowerCase().replace(/\s+/g, " ");
          return (
            normalized.includes("microsoft supplied font") &&
            normalized.includes("embedding restrictions included in this font")
          );
        })
      ) {
        throw new Error("font license evidence is not reviewed");
      }
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
            : String(error).includes("license evidence")
              ? "license-unreviewed"
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


