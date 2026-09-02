/**
 * Registry of open-licensed fonts shipped inside the Next.js package.
 *
 * Google Fonts are collected over the network at export time; bundled fonts
 * are read from `public/fonts/<family>/` so the isolated Chrome render and the
 * OOXML font packager use byte-identical faces without any network access.
 * Only the regular (400) and bold (700) faces are shipped: PowerPoint can only
 * declare those two upright faces in `p:embeddedFontLst`, and rendering the
 * browser with the same two faces keeps text measurement and native text
 * aligned (Chromium resolves 500 to the regular face and 600-900 to bold).
 */
import { readFile, stat } from "node:fs/promises";
import path from "node:path";

export interface BundledFontFace {
  readonly weight: 400 | 700;
  readonly style: "normal";
  /** WOFF2 file used for the Chromium render document. */
  readonly browserFileName: string;
  /** Static TrueType file used for the PowerPoint EOT packager. */
  readonly ooxmlFileName: string;
}

export interface BundledFontFamily {
  readonly family: string;
  /** Directory under `public/fonts/`. */
  readonly directory: string;
  readonly license: "OFL-1.1";
  readonly licenseFileName: string;
  readonly faces: readonly BundledFontFace[];
}

export const BUNDLED_FONT_FAMILIES: readonly BundledFontFamily[] = Object.freeze([
  {
    family: "Pretendard",
    directory: "pretendard",
    license: "OFL-1.1",
    licenseFileName: "LICENSE.txt",
    faces: [
      {
        weight: 400,
        style: "normal",
        browserFileName: "Pretendard-Regular.woff2",
        ooxmlFileName: "Pretendard-Regular.ttf",
      },
      {
        weight: 700,
        style: "normal",
        browserFileName: "Pretendard-Bold.woff2",
        ooxmlFileName: "Pretendard-Bold.ttf",
      },
    ],
  },
]);

const MAX_BUNDLED_FONT_FILE_BYTES = 16 * 1024 * 1024;

function normalizeFamily(value: string): string {
  return value.trim().replace(/^['"]|['"]$/g, "").replace(/\s+/g, " ").toLowerCase();
}

export function bundledFontFamily(family: string): BundledFontFamily | undefined {
  const normalized = normalizeFamily(family);
  return BUNDLED_FONT_FAMILIES.find((entry) => normalizeFamily(entry.family) === normalized);
}

export function isBundledFontFamily(family: string): boolean {
  return bundledFontFamily(family) !== undefined;
}

/**
 * Candidate directories holding `public/fonts`. The Next.js server (dev and
 * standalone) runs with `servers/nextjs` as its working directory and ships
 * `public/` beside it; the repository-root fallback covers callers that start
 * elsewhere, such as scripts run from the monorepo root.
 */
export function bundledFontPublicRoots(): string[] {
  const roots = [
    path.join(process.cwd(), "public", "fonts"),
    path.join(process.cwd(), "servers", "nextjs", "public", "fonts"),
  ];
  const appRoot = process.env.PRESENTON_APP_ROOT?.trim();
  if (appRoot) roots.push(path.join(appRoot, "servers", "nextjs", "public", "fonts"));
  return [...new Set(roots.map((root) => path.resolve(root)))];
}

/** Directories the OOXML font allowlist may scan for a bundled family; empty for unknown names. */
export function bundledFontDirectories(family: BundledFontFamily | string): string[] {
  const entry = typeof family === "string" ? bundledFontFamily(family) : family;
  if (!entry) return [];
  return bundledFontPublicRoots().map((root) => path.join(root, entry.directory));
}

export interface BundledFontFile {
  readonly family: string;
  readonly weight: 400 | 700;
  readonly fileName: string;
  readonly filePath: string;
  readonly data: Buffer;
}

const fileCache = new Map<string, Promise<BundledFontFile | undefined>>();

async function readBundledFile(
  family: BundledFontFamily,
  face: BundledFontFace,
  fileName: string,
): Promise<BundledFontFile | undefined> {
  for (const directory of bundledFontDirectories(family)) {
    const filePath = path.join(directory, fileName);
    try {
      const info = await stat(filePath);
      if (!info.isFile() || info.size < 12 || info.size > MAX_BUNDLED_FONT_FILE_BYTES) continue;
      const data = await readFile(filePath);
      return { family: family.family, weight: face.weight, fileName, filePath, data };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
      throw error;
    }
  }
  return undefined;
}

/**
 * Read one bundled browser face. Results are cached for the process lifetime
 * because the files are immutable package assets; a missing file is cached as
 * `undefined` so a stripped deployment does not stat on every slide.
 */
export function loadBundledBrowserFace(
  family: BundledFontFamily,
  face: BundledFontFace,
): Promise<BundledFontFile | undefined> {
  const key = `browser\0${family.family}\0${face.browserFileName}`;
  let pending = fileCache.get(key);
  if (!pending) {
    pending = readBundledFile(family, face, face.browserFileName);
    fileCache.set(key, pending);
  }
  return pending;
}

/** Test seam: forget cached bundled font bytes. */
export function resetBundledFontCache(): void {
  fileCache.clear();
}
