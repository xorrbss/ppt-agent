#!/usr/bin/env node
/** Run the real-browser local three-slide authored acceptance flow.
 *
 * The script intentionally does not start FastAPI or Next.  Those processes
 * must receive their own explicit local-only flags before they boot, and are
 * left running so a developer can inspect the resulting page after Cypress.
 */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const required = [
  ["AUTHORED_VNEXT_LOCAL_SLIDE_COUNT", "3"],
  ["NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED", "true"],
  ["NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT", "3"],
  ["WORKSPACE_LIVE_BACKEND", /^http:\/\/127\.0\.0\.1:\d+$/u],
  ["CYPRESS_BASE_URL", /^http:\/\/127\.0\.0\.1:\d+$/u],
];

for (const [name, expected] of required) {
  const value = process.env[name] ?? "";
  const valid = typeof expected === "string" ? value === expected : expected.test(value);
  if (!valid) {
    throw new Error(
      `Set ${name}=${typeof expected === "string" ? expected : "http://127.0.0.1:<port>"} before running the local authored happy path.`,
    );
  }
}

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const nextDirectory = path.resolve(scriptDirectory, "..");
const cypressCli = path.join(nextDirectory, "node_modules", "cypress", "bin", "cypress");
const child = spawn(
  process.execPath,
  [
    cypressCli,
    "run",
    "--e2e",
    "--config-file",
    "scripts/authored-vnext-local-happy-path.cypress.config.mjs",
    "--config",
    `baseUrl=${process.env.CYPRESS_BASE_URL}`,
    "--spec",
    "scripts/authored-vnext-local-happy-path.cy.ts",
    "--env",
    `WORKSPACE_LIVE_BACKEND=${process.env.WORKSPACE_LIVE_BACKEND}`,
  ],
  { cwd: nextDirectory, env: process.env, stdio: "inherit" },
);
child.once("exit", (code, signal) => {
  if (signal) {
    console.error(`Local authored Cypress run terminated by ${signal}.`);
    process.exitCode = 1;
    return;
  }
  process.exitCode = code ?? 1;
});
