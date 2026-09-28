#!/usr/bin/env node
// Structural layering gate.
//
// Replaces the removed 500-line file-size ratchet (see
// docs/refactoring-audit-20260929.md §5, phase 1). File length is not a
// defect on its own; import direction, cycles, and a known anti-pattern
// (copying a caller's globals() to satisfy a line-count limit) are. This
// script enforces five rules, each producing a sorted list of violating
// files:
//
//   (a) servers/fastapi/utils/**    importing `services`
//   (b) servers/fastapi/services/** importing `api`
//   (c) servers/fastapi/models/**   importing `services` or `api`
//   (d) servers/fastapi/** (excluding tests, .venv, vendor, dist, alembic)
//       containing a `globals()[...] =` assignment
//   (e) servers/nextjs/lib/** or servers/nextjs/store/** importing `@/app/`
//
// The check FAILS if a currently-violating file is missing from the
// allowlist (a new violation was introduced), and it also FAILS if the
// allowlist names a file that no longer violates or no longer exists (the
// list may only shrink — nobody may "bank" a fixed violation for later).
//
// Usage:
//   node scripts/check-structural-layering.mjs            # check
//   node scripts/check-structural-layering.mjs --write     # regenerate
//   node scripts/check-structural-layering.mjs --root DIR  # scan a
//     different root instead of the repository root (used by the test file
//     to exercise the rules against synthetic fixtures without touching the
//     real tree)

import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "..");

// The allowlist lives at <root>/scripts/structural-layering-allowlist.json.
// Deriving it from `root` (which defaults to the repository root) rather than
// from this script's own fixed location lets the test file point `--root` at
// a synthetic fixture tree and exercise --write there without ever touching
// the real, committed allowlist.
function allowlistPathFor(root) {
  return resolve(root, "scripts", "structural-layering-allowlist.json");
}

const RULE_A_RE = /^\s*(from|import)\s+services\b/m;
const RULE_B_RE = /^\s*(from|import)\s+api\b/m;
const RULE_C_RE = /^\s*(from|import)\s+(services|api)\b/m;
const RULE_D_RE = /globals\(\)\s*\[[^\]]+\]\s*=/;
const RULE_E_RE = /from\s+["']@\/app\//;

const RULE_D_EXCLUDED_SEGMENTS = ["/tests/", "/.venv/", "/vendor/", "/dist/", "/alembic/"];
const GENERIC_EXCLUDED_DIR_NAMES = new Set([
  "node_modules",
  ".venv",
  "vendor",
  "dist",
  "output",
  ".git",
  ".turbo",
]);

function toPosix(p) {
  return p.split("\\").join("/");
}

function relPosix(root, absPath) {
  return toPosix(relative(root, absPath));
}

function walk(dir, results = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return results;
  }
  for (const entry of entries) {
    if (GENERIC_EXCLUDED_DIR_NAMES.has(entry.name)) continue;
    if (entry.name.startsWith(".next")) continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full, results);
    } else {
      results.push(full);
    }
  }
  return results;
}

function readTextOrNull(absPath) {
  try {
    return readFileSync(absPath, "utf8");
  } catch {
    return null;
  }
}

function findByRule(root, subdir, extRe, contentRe, extraExclude) {
  const base = resolve(root, subdir);
  return walk(base)
    .filter((f) => extRe.test(f))
    .filter((f) => !extraExclude || !extraExclude(relPosix(root, f)))
    .filter((f) => {
      const text = readTextOrNull(f);
      return text !== null && contentRe.test(text);
    })
    .map((f) => relPosix(root, f))
    .sort();
}

const PY_EXT_RE = /\.py$/;
const TS_EXT_RE = /\.(ts|tsx|js|mjs)$/;

function ruleATest(f) {
  return !/\.test\.|\.spec\./.test(f);
}

const RULES = {
  utils_import_services: {
    label: "servers/fastapi/utils/** importing `services`",
    find: (root) =>
      findByRule(root, "servers/fastapi/utils", PY_EXT_RE, RULE_A_RE),
  },
  services_import_api: {
    label: "servers/fastapi/services/** importing `api`",
    find: (root) =>
      findByRule(root, "servers/fastapi/services", PY_EXT_RE, RULE_B_RE),
  },
  models_import_services_or_api: {
    label: "servers/fastapi/models/** importing `services` or `api`",
    find: (root) =>
      findByRule(root, "servers/fastapi/models", PY_EXT_RE, RULE_C_RE),
  },
  globals_injection: {
    label: "servers/fastapi/** containing a `globals()[...] =` assignment",
    find: (root) =>
      findByRule(root, "servers/fastapi", PY_EXT_RE, RULE_D_RE, (rel) => {
        const withSlashes = `/${rel}/`;
        return RULE_D_EXCLUDED_SEGMENTS.some((seg) => withSlashes.includes(seg));
      }),
  },
  nextjs_lib_store_import_app: {
    label: "servers/nextjs/lib|store importing `@/app/`",
    find: (root) => {
      const fromLib = findByRule(
        root,
        "servers/nextjs/lib",
        TS_EXT_RE,
        RULE_E_RE,
        (rel) => !ruleATest(rel),
      );
      const fromStore = findByRule(
        root,
        "servers/nextjs/store",
        TS_EXT_RE,
        RULE_E_RE,
        (rel) => !ruleATest(rel),
      );
      return [...fromLib, ...fromStore].sort();
    },
  },
};

export function computeCurrent(root) {
  const out = {};
  for (const [key, rule] of Object.entries(RULES)) {
    out[key] = rule.find(root);
  }
  return out;
}

export function loadAllowlist(root = repoRoot) {
  const text = readTextOrNull(allowlistPathFor(root));
  if (!text) return null;
  return JSON.parse(text);
}

export function diffAgainstAllowlist(current, allowlist) {
  const result = {};
  for (const key of Object.keys(RULES)) {
    const currentSet = new Set(current[key] ?? []);
    const allowedSet = new Set(allowlist[key] ?? []);
    result[key] = {
      newViolations: [...currentSet].filter((f) => !allowedSet.has(f)).sort(),
      stale: [...allowedSet].filter((f) => !currentSet.has(f)).sort(),
    };
  }
  return result;
}

export function ruleKeys() {
  return Object.keys(RULES);
}

function main() {
  const args = process.argv.slice(2);
  const write = args.includes("--write");
  const rootIndex = args.indexOf("--root");
  const root = rootIndex !== -1 ? resolve(args[rootIndex + 1]) : repoRoot;

  const current = computeCurrent(root);
  const allowlistPath = allowlistPathFor(root);

  if (write) {
    mkdirSync(dirname(allowlistPath), { recursive: true });
    writeFileSync(allowlistPath, `${JSON.stringify(current, null, 2)}\n`);
    let total = 0;
    for (const [key, list] of Object.entries(current)) {
      console.log(`${key}: ${list.length}`);
      total += list.length;
    }
    console.log(`wrote ${relPosix(root, allowlistPath)} (${total} entries)`);
    return;
  }

  const allowlist = loadAllowlist(root);
  if (!allowlist) {
    console.error(
      `missing allowlist at ${relPosix(root, allowlistPath)}; run with --write to create it`,
    );
    process.exitCode = 1;
    return;
  }

  const diff = diffAgainstAllowlist(current, allowlist);
  let failed = false;
  let total = 0;
  for (const [key, rule] of Object.entries(RULES)) {
    total += (allowlist[key] ?? []).length;
    const { newViolations, stale } = diff[key];
    if (newViolations.length > 0) {
      failed = true;
      console.error(`[${key}] ${rule.label}`);
      console.error("  new violation(s) not in the allowlist:");
      for (const f of newViolations) console.error(`    ${f}`);
    }
    if (stale.length > 0) {
      failed = true;
      console.error(`[${key}] ${rule.label}`);
      console.error(
        "  allowlisted file(s) that no longer violate or no longer exist (the list may only shrink):",
      );
      for (const f of stale) console.error(`    ${f}`);
    }
  }

  if (failed) {
    console.error(
      "\nstructural layering gate failed. Fix the import/pattern rather than growing " +
        "the allowlist; if an entry has genuinely been fixed, remove it (or run " +
        "`node scripts/check-structural-layering.mjs --write` and review the diff).",
    );
    process.exitCode = 1;
  } else {
    console.log(`structural layering gate passed (${total} allowlisted entries)`);
  }
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  main();
}
