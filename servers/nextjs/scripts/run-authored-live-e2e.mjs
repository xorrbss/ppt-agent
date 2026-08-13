import { writeFile } from "node:fs/promises";
import path from "node:path";

const AUTHORED_WORKER_TOKEN =
  "workspace-e2e-authored-worker-token-20260814-000000000000000000000000";

export function authoredLiveEnvironment() {
  return {
    NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED: "true",
    AUTHORED_VNEXT_OOXML_WORKER_TOKEN: AUTHORED_WORKER_TOKEN,
  };
}

export async function runAuthoredLiveE2e({
  artifactArguments,
  artifactDirectory,
  baseUrl,
  childEnvironment,
  cypressCli,
  liveBackendUrl,
  onProcess,
  runDirectory,
  spawnNode,
  waitForExit,
}) {
  const fixturePath = path.join(runDirectory, "authored-existing-90.pptx");
  const logoPath = path.resolve("public", "Logo.png");
  const { buildExisting90Fixture } = await import(
    "../lib/ooxml-vnext/fixtures/build-existing-90.mjs"
  );
  const receipt = await buildExisting90Fixture(fixturePath);
  if (receipt.slideCount !== 90 || receipt.quarantined) {
    throw new Error("The reproducible authored 90-slide fixture is not admissible.");
  }
  await writeFile(
    path.join(artifactDirectory, "authored-existing-90-receipt.json"),
    `${JSON.stringify(receipt, null, 2)}\n`,
  );
  const process = await spawnNode(
    cypressCli,
    [
      "run",
      "--e2e",
      "--spec",
      "cypress/e2e/authoredVnextLiveRoute.cy.ts",
      "--env",
      `WORKSPACE_LIVE_BACKEND=${liveBackendUrl},AUTHORED_PPTX_FIXTURE=${fixturePath},AUTHORED_LOGO_FIXTURE=${logoPath}`,
      ...artifactArguments("authored-live"),
    ],
    { ...childEnvironment, CYPRESS_BASE_URL: baseUrl },
    "cypress-authored-live",
  );
  onProcess(process);
  const result = await waitForExit(process, "Cypress authored live-route suite");
  if (result.code !== 0) {
    const error = new Error(
      `Authored Cypress suite exited with code ${result.code} (signal=${result.signal}).`,
    );
    error.exitCode = result.code || 1;
    throw error;
  }
}
