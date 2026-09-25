/** Mode-neutral, physical document geometry. Legacy geometry stays in its adapter. */
export const DOCUMENT_CANVAS_VERSION = "document-canvas/v1" as const;

export interface DocumentCanvas {
  contractVersion: typeof DOCUMENT_CANVAS_VERSION;
  logicalSize: { width: number; height: number };
  physicalSize: { widthEmu: number; heightEmu: number };
  safeArea: { x: number; y: number; width: number; height: number };
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("document_canvas_contract_invalid");
  }
  return value as Record<string, unknown>;
}

function keys(value: Record<string, unknown>, expected: string[]): void {
  if (Object.keys(value).length !== expected.length || expected.some(key => !(key in value))) {
    throw new Error("document_canvas_contract_invalid");
  }
}

function integer(value: unknown, maximum: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > maximum) {
    throw new Error("document_canvas_contract_invalid");
  }
  return value;
}

/** Convert the decimal representation exactly; do not round binary multiplication. */
function microUnits(value: unknown): bigint {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 7680) {
    throw new Error("document_canvas_contract_invalid");
  }
  const [mantissa, exponent = "0"] = String(value).toLowerCase().split("e");
  const [whole, fraction = ""] = mantissa.split(".");
  const digits = BigInt(whole + fraction);
  const shift = 6 + Number(exponent) - fraction.length;
  if (shift >= 0) return digits * 10n ** BigInt(shift);
  const divisor = 10n ** BigInt(-shift);
  if (digits % divisor !== 0n) throw new Error("document_canvas_precision_invalid");
  return digits / divisor;
}

export function parseDocumentCanvas(value: unknown): DocumentCanvas {
  const root = object(value);
  keys(root, ["contractVersion", "logicalSize", "physicalSize", "safeArea"]);
  if (root.contractVersion !== DOCUMENT_CANVAS_VERSION) throw new Error("document_canvas_version_unsupported");
  const logical = object(root.logicalSize);
  const physical = object(root.physicalSize);
  const safe = object(root.safeArea);
  keys(logical, ["width", "height"]);
  keys(physical, ["widthEmu", "heightEmu"]);
  keys(safe, ["x", "y", "width", "height"]);
  const width = integer(logical.width, 7680);
  const height = integer(logical.height, 7680);
  const widthEmu = integer(physical.widthEmu, 2147483647);
  const heightEmu = integer(physical.heightEmu, 2147483647);
  const crossAxisDelta = (
    BigInt(widthEmu) * BigInt(height) - BigInt(heightEmu) * BigInt(width)
  );
  if ((crossAxisDelta < 0n ? -crossAxisDelta : crossAxisDelta) * 2n > BigInt(widthEmu)) {
    throw new Error("document_canvas_nonuniform_scale");
  }
  const [x, y, w, h] = [safe.x, safe.y, safe.width, safe.height].map(microUnits);
  if (w <= 0n || h <= 0n || x + w > BigInt(width) * 1000000n || y + h > BigInt(height) * 1000000n) {
    throw new Error("document_canvas_safe_area_out_of_bounds");
  }
  return Object.freeze({
    contractVersion: DOCUMENT_CANVAS_VERSION,
    logicalSize: Object.freeze({ width, height }),
    physicalSize: Object.freeze({ widthEmu, heightEmu }),
    safeArea: Object.freeze({ x: Number(x) / 1e6, y: Number(y) / 1e6, width: Number(w) / 1e6, height: Number(h) / 1e6 }),
  });
}

export function documentCanvasForOrientation(orientation: "landscape" | "portrait"): DocumentCanvas {
  if (orientation !== "landscape" && orientation !== "portrait") throw new Error("document_canvas_orientation_invalid");
  const [width, height] = orientation === "landscape" ? [1188, 840] : [840, 1188];
  return parseDocumentCanvas({
    contractVersion: DOCUMENT_CANVAS_VERSION,
    logicalSize: { width, height },
    physicalSize: { widthEmu: width * 9000, heightEmu: height * 9000 },
    safeArea: { x: 60, y: 60, width: width - 120, height: height - 120 },
  });
}

export function documentCanvasCanonicalJson(value: unknown): string {
  const canvas = parseDocumentCanvas(value);
  return JSON.stringify([
    canvas.contractVersion,
    String(canvas.logicalSize.width), String(canvas.logicalSize.height),
    String(canvas.physicalSize.widthEmu), String(canvas.physicalSize.heightEmu),
    ...[canvas.safeArea.x, canvas.safeArea.y, canvas.safeArea.width, canvas.safeArea.height].map(v => String(microUnits(v))),
  ]);
}

export async function documentCanvasDigest(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(documentCanvasCanonicalJson(value));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("")}`;
}

export function documentCanvasPhysicalSize(value: DocumentCanvas) {
  const canvas = parseDocumentCanvas(value);
  const { widthEmu, heightEmu } = canvas.physicalSize;
  return { widthEmu, heightEmu, widthPt: widthEmu / 12700, heightPt: heightEmu / 12700 };
}
