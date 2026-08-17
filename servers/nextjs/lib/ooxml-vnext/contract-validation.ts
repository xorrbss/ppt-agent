import {
  DECK_MASTER_CONTRACT_VERSION,
  type DeckMasterContract,
  type DeckMasterContractValidationResult,
  type DeckMasterOwnership,
  type DeckMasterRect,
  type DeckMasterTokenValue,
  type NormalizedDeckMasterContract,
} from "./contracts.ts";

export class DeckMasterContractValidationError extends Error {
  readonly issues: readonly string[];

  constructor(issues: readonly string[]) {
    super(`Invalid DeckMasterContract:\n- ${issues.join("\n- ")}`);
    this.name = "DeckMasterContractValidationError";
    this.issues = [...issues];
  }
}

const IDENTIFIER_RE = /^(?:master|layout|placeholder|furniture):[a-z0-9._-]+$/;
const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const SHA256_RE = /^[0-9a-f]{64}$/;
const REQUIRED_THEME_COLORS = [
  "dk1",
  "lt1",
  "dk2",
  "lt2",
  "accent1",
  "accent2",
  "accent3",
  "accent4",
  "accent5",
  "accent6",
  "hlink",
  "folHlink",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function allowedKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
  path: string,
  issues: string[],
): void {
  const allowed = new Set(keys);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) issues.push(`${path}.${key} is not supported`);
  }
}

function requireRecord(
  value: unknown,
  path: string,
  issues: string[],
): Record<string, unknown> | undefined {
  if (!isRecord(value)) {
    issues.push(`${path} must be an object`);
    return undefined;
  }
  return value;
}

function requireString(value: unknown, path: string, issues: string[]): value is string {
  if (typeof value !== "string" || value.trim().length === 0) {
    issues.push(`${path} must be a non-empty string`);
    return false;
  }
  return true;
}

function requireFiniteNumber(
  value: unknown,
  path: string,
  issues: string[],
  minimum = 0,
  exclusive = false,
): value is number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    (exclusive ? value <= minimum : value < minimum)
  ) {
    issues.push(`${path} must be a finite number ${exclusive ? ">" : ">="} ${minimum}`);
    return false;
  }
  return true;
}

function validateRect(value: unknown, path: string, issues: string[]): DeckMasterRect | undefined {
  const record = requireRecord(value, path, issues);
  if (!record) return undefined;
  allowedKeys(record, ["x", "y", "width", "height"], path, issues);
  const x = requireFiniteNumber(record.x, `${path}.x`, issues);
  const y = requireFiniteNumber(record.y, `${path}.y`, issues);
  const width = requireFiniteNumber(record.width, `${path}.width`, issues, 0, true);
  const height = requireFiniteNumber(record.height, `${path}.height`, issues, 0, true);
  return x && y && width && height
    ? { x: record.x as number, y: record.y as number, width: record.width as number, height: record.height as number }
    : undefined;
}

function validateOwnership(value: unknown, path: string, issues: string[]): DeckMasterOwnership | undefined {
  const record = requireRecord(value, path, issues);
  if (!record) return undefined;
  allowedKeys(record, ["locked", "modelWritable"], path, issues);
  if (typeof record.locked !== "boolean") issues.push(`${path}.locked must be boolean`);
  if (typeof record.modelWritable !== "boolean") issues.push(`${path}.modelWritable must be boolean`);
  return typeof record.locked === "boolean" && typeof record.modelWritable === "boolean"
    ? { locked: record.locked, modelWritable: record.modelWritable }
    : undefined;
}

function validateBoundsWithinSlide(
  bounds: DeckMasterRect | undefined,
  slide: DeckMasterRect | undefined,
  path: string,
  issues: string[],
): void {
  if (!bounds || !slide) return;
  if (bounds.x + bounds.width > slide.width || bounds.y + bounds.height > slide.height) {
    issues.push(`${path} must remain inside slideSizePx`);
  }
}

function validateTextStyle(value: unknown, path: string, issues: string[]): void {
  const record = requireRecord(value, path, issues);
  if (!record) return;
  allowedKeys(record, ["fontPt", "lineHeight", "maxLines", "marginsPx"], path, issues);
  requireFiniteNumber(record.fontPt, `${path}.fontPt`, issues, 0, true);
  requireFiniteNumber(record.lineHeight, `${path}.lineHeight`, issues, 0, true);
  if (!Number.isInteger(record.maxLines) || (record.maxLines as number) < 1) {
    issues.push(`${path}.maxLines must be a positive integer`);
  }
  const margins = requireRecord(record.marginsPx, `${path}.marginsPx`, issues);
  if (margins) {
    allowedKeys(margins, ["top", "right", "bottom", "left"], `${path}.marginsPx`, issues);
    for (const side of ["top", "right", "bottom", "left"] as const) {
      requireFiniteNumber(margins[side], `${path}.marginsPx.${side}`, issues);
    }
  }
}

function validateFontManifest(value: unknown, path: string, issues: string[]): void {
  const record = requireRecord(value, path, issues);
  if (!record) return;
  allowedKeys(
    record,
    ["primary", "fallbacks", "windowsFallback", "themeScripts", "files", "windowsResolved", "digest"],
    path,
    issues,
  );
  requireString(record.primary, `${path}.primary`, issues);
  if (record.windowsFallback !== undefined) requireString(record.windowsFallback, `${path}.windowsFallback`, issues);
  if (record.digest !== undefined && (typeof record.digest !== "string" || !DIGEST_RE.test(record.digest))) {
    issues.push(`${path}.digest must be a sha256 digest`);
  }
  validateResolvedFontFiles(record.files, `${path}.files`, issues);
  if (record.fallbacks !== undefined) {
    if (!Array.isArray(record.fallbacks) || record.fallbacks.length === 0) {
      issues.push(`${path}.fallbacks must be a non-empty string array when present`);
    } else {
      const unique = new Set<string>();
      record.fallbacks.forEach((font, index) => {
        if (requireString(font, `${path}.fallbacks[${index}]`, issues)) {
          if (unique.has(font)) issues.push(`${path}.fallbacks must not contain duplicates`);
          unique.add(font);
        }
      });
    }
  }
  const scripts = requireRecord(record.themeScripts, `${path}.themeScripts`, issues);
  if (scripts) {
    allowedKeys(scripts, ["latin", "eastAsian", "complexScript", "Hang", "Jpan", "Hans", "Hant"], `${path}.themeScripts`, issues);
    requireString(scripts.latin, `${path}.themeScripts.latin`, issues);
    for (const key of ["eastAsian", "complexScript", "Hang", "Jpan", "Hans", "Hant"] as const) {
      if (scripts[key] !== undefined) requireString(scripts[key], `${path}.themeScripts.${key}`, issues);
    }
  }
  if (record.windowsResolved !== undefined) {
    const resolved = requireRecord(record.windowsResolved, `${path}.windowsResolved`, issues);
    if (resolved) {
      allowedKeys(resolved, ["state", "cloud", "embedded", "files"], `${path}.windowsResolved`, issues);
      requireString(resolved.state, `${path}.windowsResolved.state`, issues);
      if (typeof resolved.cloud !== "boolean") issues.push(`${path}.windowsResolved.cloud must be boolean`);
      if (typeof resolved.embedded !== "boolean") issues.push(`${path}.windowsResolved.embedded must be boolean`);
      validateResolvedFontFiles(resolved.files, `${path}.windowsResolved.files`, issues, true);
    }
  }
}

function validateResolvedFontFiles(
  value: unknown,
  path: string,
  issues: string[],
  required = false,
): void {
  if (value === undefined && !required) return;
  if (!Array.isArray(value)) {
    issues.push(`${path} must be an array`);
    return;
  }
  const digests = new Set<string>();
  value.forEach((file, index) => {
    const filePath = `${path}[${index}]`;
    const record = requireRecord(file, filePath, issues);
    if (!record) return;
    allowedKeys(record, ["style", "path", "version", "sizeBytes", "sha256"], filePath, issues);
    requireString(record.style, `${filePath}.style`, issues);
    requireString(record.path, `${filePath}.path`, issues);
    requireString(record.version, `${filePath}.version`, issues);
    if (!Number.isInteger(record.sizeBytes) || (record.sizeBytes as number) <= 0) {
      issues.push(`${filePath}.sizeBytes must be a positive integer`);
    }
    if (typeof record.sha256 !== "string" || !SHA256_RE.test(record.sha256)) {
      issues.push(`${filePath}.sha256 must be 64 lowercase hexadecimal characters`);
    } else if (digests.has(record.sha256)) {
      issues.push(`${path} must not contain duplicate file digests`);
    } else {
      digests.add(record.sha256);
    }
  });
}

function validateTheme(value: unknown, path: string, issues: string[]): void {
  const record = requireRecord(value, path, issues);
  if (!record) return;
  allowedKeys(record, ["name", "colors"], path, issues);
  requireString(record.name, `${path}.name`, issues);
  const colors = requireRecord(record.colors, `${path}.colors`, issues);
  if (!colors) return;
  for (const key of REQUIRED_THEME_COLORS) {
    if (!(key in colors)) issues.push(`${path}.colors.${key} is required`);
  }
  for (const [key, color] of Object.entries(colors)) {
    if (!/^[A-Za-z][A-Za-z0-9._-]*$/.test(key)) issues.push(`${path}.colors.${key} has an invalid token name`);
    if (typeof color !== "string" || !HEX_COLOR_RE.test(color)) {
      issues.push(`${path}.colors.${key} must be a six-digit hex color`);
    }
  }
}

function validateTokens(value: unknown, path: string, issues: string[]): void {
  if (value === undefined) return;
  const record = requireRecord(value, path, issues);
  if (!record) return;
  for (const [key, token] of Object.entries(record)) {
    if (!/^[a-z][a-z0-9._-]*$/i.test(key)) issues.push(`${path}.${key} has an invalid token name`);
    if (!["string", "number", "boolean"].includes(typeof token) || (typeof token === "number" && !Number.isFinite(token))) {
      issues.push(`${path}.${key} must be a finite primitive value`);
    }
  }
}

export function validateDeckMasterContract(input: unknown): DeckMasterContractValidationResult {
  const issues: string[] = [];
  const root = requireRecord(input, "deckMaster", issues);
  if (!root) return { ok: false, issues };
  allowedKeys(root, ["contractVersion", "id", "name", "slideSizePx", "theme", "tokens", "fontManifest", "furniture", "layouts", "authoredManifest", "digest"], "deckMaster", issues);
  if (
    root.authoredManifest !== undefined &&
    (!isRecord(root.authoredManifest) ||
      root.authoredManifest.contractVersion !== DECK_MASTER_CONTRACT_VERSION ||
      typeof root.authoredManifest.masterId !== "string")
  ) {
    issues.push("deckMaster.authoredManifest must be a canonical authored deck master");
  }
  if (root.contractVersion !== undefined && root.contractVersion !== DECK_MASTER_CONTRACT_VERSION) {
    issues.push(`deckMaster.contractVersion must equal ${DECK_MASTER_CONTRACT_VERSION}`);
  }
  if (requireString(root.id, "deckMaster.id", issues) && !/^master:[a-z0-9._-]+$/.test(root.id)) {
    issues.push("deckMaster.id must start with master:");
  }
  requireString(root.name, "deckMaster.name", issues);
  if (root.digest !== undefined && (typeof root.digest !== "string" || !DIGEST_RE.test(root.digest))) {
    issues.push("deckMaster.digest must be a sha256 digest");
  }
  const slideSize = validateRect(root.slideSizePx, "deckMaster.slideSizePx", issues);
  validateTheme(root.theme, "deckMaster.theme", issues);
  validateTokens(root.tokens, "deckMaster.tokens", issues);
  validateFontManifest(root.fontManifest, "deckMaster.fontManifest", issues);

  const furnitureIds = new Set<string>();
  if (!Array.isArray(root.furniture) || root.furniture.length === 0) {
    issues.push("deckMaster.furniture must be a non-empty array");
  } else {
    root.furniture.forEach((item, index) => {
      const path = `deckMaster.furniture[${index}]`;
      const record = requireRecord(item, path, issues);
      if (!record) return;
      allowedKeys(record, ["id", "kind", "text", "bounds", "fill", "stroke", "assetId", "assetSha256", "visibility", "ownership"], path, issues);
      if (requireString(record.id, `${path}.id`, issues)) {
        if (!/^furniture:[a-z0-9._-]+$/.test(record.id)) issues.push(`${path}.id must start with furniture:`);
        if (furnitureIds.has(record.id)) issues.push(`${path}.id must be unique`);
        furnitureIds.add(record.id);
      }
      if (!["rect", "line", "text", "logo", "pageNumber"].includes(record.kind as string)) issues.push(`${path}.kind is unsupported`);
      if (record.kind === "text") requireString(record.text, `${path}.text`, issues);
      if (record.kind === "logo" && record.assetId !== undefined && (
        typeof record.assetId !== "string" || !/^asset:[a-z0-9][a-z0-9._-]*$/.test(record.assetId)
      )) issues.push(`${path}.assetId must be an asset identifier`);
      if (record.kind === "logo" && record.assetSha256 !== undefined && (
        typeof record.assetSha256 !== "string" || !/^[0-9a-f]{64}$/.test(record.assetSha256)
      )) issues.push(`${path}.assetSha256 must be lowercase sha256 hex`);
      if (record.kind === "logo" && Boolean(record.assetId) !== Boolean(record.assetSha256)) {
        issues.push(`${path}.assetId and assetSha256 must be configured together`);
      }
      if (record.visibility !== undefined && !["always", "exceptCover", "coverOnly", "hidden"].includes(record.visibility as string)) {
        issues.push(`${path}.visibility is unsupported`);
      }
      const bounds = validateRect(record.bounds, `${path}.bounds`, issues);
      validateBoundsWithinSlide(bounds, slideSize, `${path}.bounds`, issues);
      const ownership = validateOwnership(record.ownership, `${path}.ownership`, issues);
      if (ownership && (!ownership.locked || ownership.modelWritable)) {
        issues.push(`${path}.ownership must keep master furniture locked and not model-writable`);
      }
      for (const colorKey of ["fill", "stroke"] as const) {
        const color = record[colorKey];
        if (color !== undefined && (typeof color !== "string" || !HEX_COLOR_RE.test(color))) {
          issues.push(`${path}.${colorKey} must be a six-digit hex color`);
        }
      }
      if (record.kind === "line" && record.stroke === undefined) issues.push(`${path}.stroke is required for a line`);
      if (["rect", "text", "pageNumber"].includes(record.kind as string) && record.fill === undefined) {
        issues.push(`${path}.fill is required for ${String(record.kind)}`);
      }
    });
  }

  const layoutIds = new Set<string>();
  const layoutNames = new Set<string>();
  const placeholderIds = new Set<string>();
  const layoutKinds = new Map<string, number>();
  if (!Array.isArray(root.layouts) || root.layouts.length < 4 || root.layouts.length > 32) {
    issues.push("deckMaster.layouts must contain 4 to 32 layouts");
  } else {
    root.layouts.forEach((item, index) => {
      const path = `deckMaster.layouts[${index}]`;
      const record = requireRecord(item, path, issues);
      if (!record) return;
      allowedKeys(record, ["id", "name", "kind", "placeholders"], path, issues);
      if (requireString(record.id, `${path}.id`, issues)) {
        if (!/^layout:[a-z0-9._-]+$/.test(record.id)) issues.push(`${path}.id must start with layout:`);
        if (layoutIds.has(record.id)) issues.push(`${path}.id must be unique`);
        layoutIds.add(record.id);
      }
      if (requireString(record.name, `${path}.name`, issues)) {
        if (layoutNames.has(record.name)) issues.push(`${path}.name must be unique`);
        layoutNames.add(record.name);
      }
      if (!["cover", "section", "standard", "long-title"].includes(record.kind as string)) {
        issues.push(`${path}.kind is unsupported`);
      } else {
        layoutKinds.set(record.kind as string, (layoutKinds.get(record.kind as string) ?? 0) + 1);
      }
      const placeholderIndices = new Set<number>();
      const placeholderTypes = new Set<string>();
      if (!Array.isArray(record.placeholders) || record.placeholders.length < 2) {
        issues.push(`${path}.placeholders must contain title and body/content placeholders`);
        return;
      }
      record.placeholders.forEach((placeholder, placeholderIndex) => {
        const placeholderPath = `${path}.placeholders[${placeholderIndex}]`;
        const placeholderRecord = requireRecord(placeholder, placeholderPath, issues);
        if (!placeholderRecord) return;
        allowedKeys(placeholderRecord, ["id", "type", "index", "bounds", "ownership", "textStyle"], placeholderPath, issues);
        if (requireString(placeholderRecord.id, `${placeholderPath}.id`, issues)) {
          if (!/^placeholder:[a-z0-9._-]+$/.test(placeholderRecord.id)) issues.push(`${placeholderPath}.id must start with placeholder:`);
          if (placeholderIds.has(placeholderRecord.id)) issues.push(`${placeholderPath}.id must be globally unique`);
          placeholderIds.add(placeholderRecord.id);
        }
        if (!["title", "body", "content"].includes(placeholderRecord.type as string)) {
          issues.push(`${placeholderPath}.type is unsupported`);
        } else {
          placeholderTypes.add(placeholderRecord.type as string);
        }
        if (!Number.isInteger(placeholderRecord.index) || (placeholderRecord.index as number) < 0) {
          issues.push(`${placeholderPath}.index must be a non-negative integer`);
        } else if (placeholderIndices.has(placeholderRecord.index as number)) {
          issues.push(`${placeholderPath}.index must be unique within its layout`);
        } else {
          placeholderIndices.add(placeholderRecord.index as number);
        }
        const bounds = validateRect(placeholderRecord.bounds, `${placeholderPath}.bounds`, issues);
        validateBoundsWithinSlide(bounds, slideSize, `${placeholderPath}.bounds`, issues);
        const ownership = validateOwnership(placeholderRecord.ownership, `${placeholderPath}.ownership`, issues);
        if (ownership && (ownership.locked || !ownership.modelWritable)) {
          issues.push(`${placeholderPath}.ownership must be unlocked and model-writable`);
        }
        validateTextStyle(placeholderRecord.textStyle, `${placeholderPath}.textStyle`, issues);
        if (placeholderRecord.type === "title" && isRecord(placeholderRecord.textStyle)) {
          const maxLines = placeholderRecord.textStyle.maxLines;
          if (record.kind === "standard" && typeof maxLines === "number" && maxLines > 2) {
            issues.push(`${placeholderPath}.textStyle.maxLines must be at most 2 for standard`);
          }
          if (record.kind === "long-title" && typeof maxLines === "number" && maxLines < 3) {
            issues.push(`${placeholderPath}.textStyle.maxLines must be at least 3 for long-title`);
          }
        }
      });
      if (!placeholderTypes.has("title")) issues.push(`${path} requires a title placeholder`);
      if (!placeholderTypes.has("body") && !placeholderTypes.has("content")) {
        issues.push(`${path} requires a body or content placeholder`);
      }
    });
  }
  if (!layoutKinds.get("cover")) issues.push("deckMaster.layouts requires a cover layout");
  if (!layoutKinds.get("section")) issues.push("deckMaster.layouts requires a section layout");
  if (!layoutKinds.get("standard")) issues.push("deckMaster.layouts requires a standard layout");
  if (!layoutKinds.get("long-title")) issues.push("deckMaster.layouts requires a long-title layout");

  return { ok: issues.length === 0, issues };
}

export function assertDeckMasterContract(input: unknown): asserts input is DeckMasterContract {
  const result = validateDeckMasterContract(input);
  if (!result.ok) throw new DeckMasterContractValidationError(result.issues);
}

export function normalizeDeckMasterContract(input: unknown): NormalizedDeckMasterContract {
  assertDeckMasterContract(input);
  const contract = input as DeckMasterContract;
  const defaultTokens: Record<string, DeckMasterTokenValue> = {
    "color.background": "bg1",
    "color.text.primary": "tx1",
    "color.text.secondary": "tx2",
    "font.body": contract.fontManifest.primary,
    "font.title": contract.fontManifest.primary,
  };
  return {
    ...contract,
    contractVersion: DECK_MASTER_CONTRACT_VERSION,
    tokens: Object.fromEntries(
      Object.entries({ ...defaultTokens, ...(contract.tokens ?? {}) }).sort(([left], [right]) =>
        left.localeCompare(right, "en"),
      ),
    ),
  };
}

export function deckMasterRole(identifier: string): string {
  if (!IDENTIFIER_RE.test(identifier)) throw new Error(`Invalid semantic identifier: ${identifier}`);
  return identifier.slice(identifier.indexOf(":") + 1);
}

export const DECK_MASTER_REQUIRED_THEME_COLORS = REQUIRED_THEME_COLORS;
