import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(root, "../..");
const owner = "app/(presentation-generator)/services/api/header.ts";
const roots = ["app", "lib", "components", "scripts"];

function* sourceFiles(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) yield* sourceFiles(file);
    else if (/\.(?:ts|tsx|js|jsx|mjs)$/.test(entry.name) && !/\.test\./.test(entry.name)) yield file;
  }
}

function fetchCalls(source) {
  const calls = [];
  const start = /\b(?:fetch|fetchImpl)\s*\(/g;
  for (const match of source.matchAll(start)) {
    let depth = 1;
    let quote = "";
    let escaped = false;
    let index = match.index + match[0].length;
    for (; index < source.length && depth; index++) {
      const char = source[index];
      if (quote) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === quote) quote = "";
      } else if (char === "'" || char === '"' || char === "`") quote = char;
      else if (char === "(") depth++;
      else if (char === ")") depth--;
    }
    calls.push(source.slice(match.index, index));
  }
  return calls;
}

function errorsForSource(file, source) {
  const errors = [];
  if (file !== owner && file !== "repo-scripts/lib/ppt-agent-client.mjs" && /["']X-Document-Canvas-Contracts["']/.test(source)) {
    errors.push(`${file}: raw canvas header`);
  }
  for (const call of fetchCalls(source)) {
    if (!call.includes("/api/v1/ppt/presentation/")) continue;
    if (/\bmethod:\s*["'](?:POST|PUT|PATCH|DELETE)["']/.test(call) && !call.includes("/restore")) continue;
    if (/\/presentation\/(?:all|generate|status|create|prepare|update|public-v2|authored-emission|canonical)\b/.test(call)) continue;
    if (/\/presentation\/\$\{[^}]+\}\/(?:share|generation-info|slides|remake|quality-review|retemplate|authored-emission)\b/.test(call)) continue;
    if (!/headers:\s*(?:getHeader|getPublicHeader)\s*\(|\.\.\.getPublicHeader\s*\(/.test(call)) {
      errors.push(`${file}: presentation read bypasses the canvas header helper`);
    }
  }
  return errors;
}

test("presentation HTTP readers use the shared canvas header owner", () => {
  const errors = [];
  for (const directory of roots) {
    for (const file of sourceFiles(path.join(root, directory))) {
      const relative = path.relative(root, file).replaceAll(path.sep, "/");
      errors.push(...errorsForSource(relative, readFileSync(file, "utf8")));
    }
  }
  for (const file of sourceFiles(path.join(repositoryRoot, "scripts"))) {
    const relative = `repo-scripts/${path.relative(path.join(repositoryRoot, "scripts"), file).replaceAll(path.sep, "/")}`;
    errors.push(...errorsForSource(relative, readFileSync(file, "utf8")));
  }
  assert.deepEqual(errors, []);
});

test("source scan rejects a new read with missing or raw headers", () => {
  const url = "`/api/v1/ppt/presentation/${id}`";
  assert.equal(errorsForSource("app/new-client.ts", `fetch(${url}, { method: "GET" })`).length, 1);
  assert.equal(errorsForSource("app/new-client.ts", `fetch(${url}, { headers: { "X-Document-Canvas-Contracts": "document-canvas/v1" } })`).length, 2);
});
