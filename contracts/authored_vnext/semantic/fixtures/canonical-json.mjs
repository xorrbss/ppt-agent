import crypto from "node:crypto";

/**
 * Authored semantic contract canonical JSON.
 *
 * The wire contract deliberately uses one representation for every finite
 * number: a normalized scientific form (except exponent zero). This avoids
 * the Python/JavaScript differences around exponent padding and large fixed
 * point values while retaining JSON numeric values.
 */
export function canonicalNumber(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError("canonical JSON only accepts finite numbers");
  }
  if (Object.is(value, -0) || value === 0) return "0";
  const negative = value < 0;
  let source = String(Math.abs(value));
  let [mantissa, exponentPart] = source.toLowerCase().split("e");
  const explicitExponent = exponentPart ? Number(exponentPart) : 0;
  const [whole, fraction = ""] = mantissa.split(".");
  const digitsBeforeDecimal = whole.length;
  let digits = `${whole}${fraction}`;
  const firstNonZero = digits.search(/[1-9]/);
  if (firstNonZero < 0) return "0";
  digits = digits.slice(firstNonZero).replace(/0+$/, "");
  const exponent = explicitExponent + digitsBeforeDecimal - firstNonZero - 1;
  const coefficient = digits.length === 1 ? digits : `${digits[0]}.${digits.slice(1)}`;
  const body = exponent === 0 ? coefficient : `${coefficient}e${exponent}`;
  return negative ? `-${body}` : body;
}

function utf16Compare(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

export function canonicalJson(value) {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value.normalize("NFC"));
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return canonicalNumber(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (typeof value === "object") {
    const entries = Object.entries(value)
      .map(([key, entry]) => [key.normalize("NFC"), entry])
      .sort(([left], [right]) => utf16Compare(left, right));
    if (new Set(entries.map(([key]) => key)).size !== entries.length) {
      throw new TypeError("canonical JSON object has normalization-colliding keys");
    }
    return `{${entries.map(([key, entry]) => `${JSON.stringify(key)}:${canonicalJson(entry)}`).join(",")}}`;
  }
  throw new TypeError(`unsupported canonical JSON value: ${typeof value}`);
}

export function canonicalSha256(value) {
  return `sha256:${crypto.createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}

export function bytesSha256(bytes) {
  return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`;
}
