#!/usr/bin/env node

import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";

const traverse = traverseModule.default ?? traverseModule;
const SOURCE_ROOTS = ["app", "components"];
const SOURCE_EXTENSION = /\.(?:[cm]?[jt]sx?)$/;
const BASELINE_PATH = path.join("i18n", "hardcoded-ui-baseline.json");
const USER_TEXT_ATTRIBUTES = new Set([
  "alt",
  "aria-description",
  "aria-label",
  "caption",
  "cancelText",
  "confirmText",
  "description",
  "emptyMessage",
  "heading",
  "label",
  "placeholder",
  "title",
  "tooltip",
]);
const USER_TEXT_PROPERTIES = new Set([
  "alt",
  "ariaDescription",
  "ariaLabel",
  "caption",
  "cancelText",
  "confirmText",
  "description",
  "emptyMessage",
  "heading",
  "label",
  "placeholder",
  "title",
  "tooltip",
]);
const MESSAGE_CALLS = new Set([
  "alert",
  "confirm",
  "error",
  "info",
  "loading",
  "prompt",
  "success",
  "warning",
]);
const TECHNICAL_TOKEN = /^(?:AI|API|CSV|CSS|Ctrl|Cmd|GB|HTML|JSON|KB|Linux|LLM|MB|OCR|PDF|PNG|PPTX|Presenton|SVG|TSV|URL|Windows|macOS)(?:[\s/+|·-]*(?:AI|API|CSV|CSS|Ctrl|Cmd|GB|HTML|JSON|KB|Linux|LLM|MB|OCR|PDF|PNG|PPTX|Presenton|SVG|TSV|URL|Windows|macOS))*[.!?]?$/iu;

function toPosix(value) {
  return value.split(path.sep).join("/");
}

function normalizeText(value) {
  return String(value).replace(/\s+/gu, " ").trim();
}

export function isLikelyUserText(value) {
  const text = normalizeText(value);
  if (!text || !/\p{L}/u.test(text)) return false;
  if (TECHNICAL_TOKEN.test(text)) return false;
  if (/^(?:https?:|data:|blob:|mailto:|\/|\.\/|\.\.\/)/iu.test(text)) return false;
  if (/^[\w.+-]+\/[\w.+-]+$/u.test(text)) return false;
  if (/^#[\da-f]{3,8}$/iu.test(text)) return false;
  if (/^(?:[\w.-]+\.)+(?:com|io|net|org|svg|png|jpe?g|webp|gif|pdf|pptx)$/iu.test(text)) {
    return false;
  }
  return true;
}

function templateText(node) {
  return node.quasis
    .map((quasi, index) => {
      const expression = index < node.expressions.length ? " {…} " : "";
      return `${quasi.value.cooked ?? quasi.value.raw}${expression}`;
    })
    .join("");
}

function staticText(node) {
  if (!node) return null;
  if (node.type === "StringLiteral") return node.value;
  if (node.type === "TemplateLiteral") return templateText(node);
  if (node.type === "TSAsExpression" || node.type === "TSTypeAssertion" || node.type === "TSNonNullExpression") {
    return staticText(node.expression);
  }
  if (node.type === "BinaryExpression" && node.operator === "+") {
    const left = staticText(node.left);
    const right = staticText(node.right);
    if (left !== null || right !== null) return `${left ?? " {…} "}${right ?? " {…} "}`;
  }
  return null;
}

function propertyName(node) {
  if (!node || node.computed) return null;
  if (node.key?.type === "Identifier") return node.key.name;
  if (node.key?.type === "StringLiteral") return node.key.value;
  return null;
}

function jsxAttributeName(node) {
  if (node.name?.type === "JSXIdentifier") return node.name.name;
  if (node.name?.type === "JSXNamespacedName") {
    return `${node.name.namespace.name}:${node.name.name.name}`;
  }
  return null;
}

function calleeName(node) {
  if (node.type === "Identifier") return node.name;
  if (node.type !== "MemberExpression" && node.type !== "OptionalMemberExpression") return null;
  if (!node.computed && node.property.type === "Identifier") return node.property.name;
  if (node.computed && node.property.type === "StringLiteral") return node.property.value;
  return null;
}

function collectRenderableExpression(node, add, kind) {
  if (!node) return;
  const direct = staticText(node);
  if (direct !== null) {
    add(node, kind, direct);
    return;
  }
  switch (node.type) {
    case "ConditionalExpression":
      collectRenderableExpression(node.consequent, add, kind);
      collectRenderableExpression(node.alternate, add, kind);
      break;
    case "LogicalExpression":
      collectRenderableExpression(node.right, add, kind);
      break;
    case "ArrayExpression":
      for (const element of node.elements) collectRenderableExpression(element, add, kind);
      break;
    case "SequenceExpression":
      collectRenderableExpression(node.expressions.at(-1), add, kind);
      break;
    case "TSAsExpression":
    case "TSTypeAssertion":
    case "TSNonNullExpression":
      collectRenderableExpression(node.expression, add, kind);
      break;
    default:
      break;
  }
}

export function collectHardcodedUiText(source, file = "fixture.tsx") {
  const ast = parse(source, {
    sourceType: "unambiguous",
    errorRecovery: false,
    plugins: ["jsx", "typescript", "decorators-legacy", "importAttributes"],
  });
  const occurrences = [];
  const add = (node, kind, rawText) => {
    const text = normalizeText(rawText);
    if (!isLikelyUserText(text)) return;
    occurrences.push({
      file: toPosix(file),
      kind,
      line: node.loc?.start.line ?? 1,
      text,
    });
  };

  traverse(ast, {
    JSXText({ node }) {
      add(node, "jsx-text", node.value);
    },
    JSXAttribute({ node }) {
      const name = jsxAttributeName(node);
      if (!name || !USER_TEXT_ATTRIBUTES.has(name) || !node.value) return;
      if (node.value.type === "StringLiteral") {
        add(node.value, `jsx-attribute:${name}`, node.value.value);
      } else if (node.value.type === "JSXExpressionContainer") {
        collectRenderableExpression(node.value.expression, add, `jsx-attribute:${name}`);
      }
    },
    JSXExpressionContainer({ node, parent }) {
      if (parent.type === "JSXAttribute") return;
      collectRenderableExpression(node.expression, add, "jsx-expression");
    },
    ObjectProperty({ node }) {
      const name = propertyName(node);
      if (!name || !USER_TEXT_PROPERTIES.has(name)) return;
      const text = staticText(node.value);
      if (text !== null) add(node.value, `ui-property:${name}`, text);
    },
    CallExpression({ node }) {
      const name = calleeName(node.callee);
      if (!name || !MESSAGE_CALLS.has(name)) return;
      const isBrowserDialog =
        ["alert", "confirm", "prompt"].includes(name) &&
        (node.callee.type === "Identifier" ||
          (node.callee.type === "MemberExpression" &&
            node.callee.object.type === "Identifier" &&
            node.callee.object.name === "window"));
      const isToast =
        ["error", "info", "loading", "success", "warning"].includes(name) &&
        node.callee.type === "MemberExpression" &&
        node.callee.object.type === "Identifier" &&
        ["notify", "toast", "sonnerToast"].includes(node.callee.object.name);
      if (!isBrowserDialog && !isToast) return;
      for (const argument of node.arguments.slice(0, 2)) {
        if (argument.type === "SpreadElement" || argument.type === "ArgumentPlaceholder") continue;
        const text = staticText(argument);
        if (text !== null) add(argument, `message-call:${name}`, text);
      }
    },
  });

  return occurrences;
}

export function isSourceFileIncluded(file) {
  const normalized = toPosix(file);
  if (!SOURCE_EXTENSION.test(normalized)) return false;
  if (!SOURCE_ROOTS.some((root) => normalized === root || normalized.startsWith(`${root}/`))) return false;
  if (/(?:^|\/)(?:__fixtures__|__snapshots__|presentation-templates|vendor)(?:\/|$)/u.test(normalized)) return false;
  if (/\.(?:cy|test|spec)\.[cm]?[jt]sx?$/u.test(normalized)) return false;
  return true;
}

function walk(directory, files) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name.startsWith(".") || entry.name === "node_modules") continue;
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(target, files);
    else files.push(target);
  }
}

export function scanWorkspace(root = process.cwd()) {
  const files = [];
  for (const sourceRoot of SOURCE_ROOTS) walk(path.join(root, sourceRoot), files);
  const occurrences = [];
  for (const absoluteFile of files) {
    const file = toPosix(path.relative(root, absoluteFile));
    if (!isSourceFileIncluded(file)) continue;
    occurrences.push(...collectHardcodedUiText(readFileSync(absoluteFile, "utf8"), file));
  }
  return occurrences;
}

function signature(occurrence) {
  return `${occurrence.file}\u0000${occurrence.kind}\u0000${occurrence.text}`;
}

function fingerprint(occurrence) {
  return createHash("sha256").update(signature(occurrence)).digest("hex").slice(0, 20);
}

export function baselineEntries(occurrences) {
  const grouped = new Map();
  for (const occurrence of occurrences) {
    const key = signature(occurrence);
    const existing = grouped.get(key);
    if (existing) existing.count += 1;
    else grouped.set(key, { file: occurrence.file, kind: occurrence.kind, text: occurrence.text, count: 1 });
  }
  return [...grouped.values()].sort((a, b) =>
    a.file.localeCompare(b.file) || a.kind.localeCompare(b.kind) || a.text.localeCompare(b.text),
  );
}

export function baselineFingerprints(occurrences) {
  const counts = {};
  for (const occurrence of occurrences) {
    const key = fingerprint(occurrence);
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)));
}

export function findNewHardcodedOccurrences(occurrences, baseline) {
  const usesFingerprints = !Array.isArray(baseline);
  const allowed = usesFingerprints
    ? new Map(Object.entries(baseline))
    : new Map(baseline.map((entry) => [signature(entry), entry.count]));
  const seen = new Map();
  const regressions = [];
  for (const occurrence of occurrences) {
    const key = usesFingerprints ? fingerprint(occurrence) : signature(occurrence);
    const count = (seen.get(key) ?? 0) + 1;
    seen.set(key, count);
    if (count > (allowed.get(key) ?? 0)) regressions.push(occurrence);
  }
  return regressions;
}

function runCli() {
  const root = process.cwd();
  const baselineFile = path.join(root, BASELINE_PATH);
  const occurrences = scanWorkspace(root);
  if (process.argv.includes("--write-baseline")) {
    const baseline = {
      version: 2,
      baseRevision: "74eeb165",
      explanation: "Existing UI literals are frozen; additions must use the ko/en catalogs.",
      fingerprints: baselineFingerprints(occurrences),
    };
    writeFileSync(baselineFile, `${JSON.stringify(baseline, null, 2)}\n`, "utf8");
    console.log(`i18n hardcoding baseline: wrote ${Object.keys(baseline.fingerprints).length} fingerprints to ${BASELINE_PATH}`);
    return;
  }

  let baseline;
  try {
    baseline = JSON.parse(readFileSync(baselineFile, "utf8"));
  } catch (error) {
    console.error(`i18n hardcoding gate: cannot read ${BASELINE_PATH}`);
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
    return;
  }
  const frozen = baseline.fingerprints ?? baseline.entries ?? [];
  const regressions = findNewHardcodedOccurrences(occurrences, frozen);
  if (regressions.length === 0) {
    const frozenCount = Array.isArray(frozen) ? frozen.length : Object.keys(frozen).length;
    console.log(`i18n hardcoding gate: passed (${frozenCount} frozen fingerprints)`);
    return;
  }
  console.error("i18n hardcoding gate: new user-facing literals must use useTranslations/getServerI18n.");
  for (const occurrence of regressions.slice(0, 80)) {
    console.error(`  ${occurrence.file}:${occurrence.line} [${occurrence.kind}] ${JSON.stringify(occurrence.text)}`);
  }
  if (regressions.length > 80) console.error(`  …and ${regressions.length - 80} more`);
  process.exitCode = 1;
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) runCli();
