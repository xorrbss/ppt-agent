/**
 * File-system access for the fonts shipped inside the Next.js package.
 *
 * The manifest (which family/weight maps to which file) lives in
 * bundled-fonts-manifest.ts; this module only locates `public/fonts` on disk
 * and reads the bytes, so the isolated Chrome render and the OOXML font
 * packager use byte-identical faces without any network access.
 */
import { readFile, stat } from "node:fs/promises";
import path from "node:path";

import {
  type BundledFontFace,
  type BundledFontFamily,
  bundledFontFamily,
} from "./bundled-fonts-manifest.ts";

export {
  BUNDLED_FONT_FAMILIES,
  BUNDLED_FONT_PUBLIC_PREFIX,
  HANGUL_CAPABLE_BUNDLED_FAMILY_KEYS,
  bundledFontFamily,
  bundledFontPublicUrl,
  isBundledFontFamily,
  normalizeBundledFamilyName,
  referencedBundledFamilies,
  resolveBundledFontFace,
} from "./bundled-fonts-manifest.ts";
export type {
  BundledFontFace,
  BundledFontFaceRequest,
  BundledFontFamily,
  BundledFontWeight,
} from "./bundled-fonts-manifest.ts";

const MAX_BUNDLED_FONT_FILE_BYTES = 16 * 1024 * 1024;

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
