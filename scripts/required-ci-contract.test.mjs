import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import { test } from "node:test";

const read = (filePath) =>
  readFileSync(new URL(`../${filePath}`, import.meta.url), "utf8");
const readJson = (filePath) => JSON.parse(read(filePath));

function workflowJob(source, jobName) {
  source = source.replaceAll("\r\n", "\n");
  const marker = `\n  ${jobName}:\n`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `workflow job ${jobName} is missing`);
  const bodyStart = start + marker.length;
  const remainder = source.slice(bodyStart);
  const nextJob = remainder.search(/\n  [a-zA-Z0-9_-]+:\n/u);
  return nextJob === -1 ? remainder : remainder.slice(0, nextJob);
}

test("importing the workspace runner does not install process signal handlers", () => {
  const runnerUrl = new URL(
    `../servers/nextjs/scripts/run-workspace-e2e.mjs?signal-probe=${randomUUID()}`,
    import.meta.url,
  ).href;
  const probe = spawnSync(
    process.execPath,
    [
      "--input-type=module",
      "--eval",
      `const before = [process.listenerCount("SIGINT"), process.listenerCount("SIGTERM")]; await import(${JSON.stringify(
        runnerUrl,
      )}); const after = [process.listenerCount("SIGINT"), process.listenerCount("SIGTERM")]; process.stdout.write(JSON.stringify({before, after}));`,
    ],
    { encoding: "utf8", timeout: 10_000 },
  );
  assert.equal(probe.status, 0, probe.stderr);
  const counts = JSON.parse(probe.stdout);
  assert.deepEqual(counts.after, counts.before);
});

test("workspace E2E is a bounded, diagnostic PR gate on GitHub", () => {
  const workflow = read(".github/workflows/test-all.yml");
  const job = workflowJob(workflow, "workspace-e2e");

  assert.match(workflow, /pull_request:\s*\n\s*branches:\s*\[\s*main\s*\]/u);
  assert.match(job, /name: Workspace chat-first browser E2E/u);
  assert.match(job, /runs-on: ubuntu-latest/u);
  assert.match(job, /timeout-minutes: 25/u);
  assert.match(
    job,
    /WORKSPACE_E2E_ARTIFACT_DIR:.*test-artifacts\/workspace-e2e/u,
  );
  assert.match(job, /xvfb-run -a npm run test:workspace-e2e/u);
  assert.match(
    job,
    /name: Upload workspace E2E diagnostics\s*\n\s*if: always\(\)/u,
  );
  assert.match(job, /test-artifacts\/workspace-e2e/u);
  assert.match(job, /if-no-files-found: warn/u);
  assert.match(job, /retention-days: 14/u);
});

test("the required FastAPI job runs production-lifecycle Gate E exactly once", () => {
  const workflow = read(".github/workflows/test-all.yml");
  const job = workflowJob(workflow, "test-fastapi");
  const gate = "tests/integration/test_workspace_production_lifecycle_gate.py";

  assert.match(
    job,
    /name: Run production lifecycle Gate E \(real app \+ worker processes\)/u,
  );
  assert.ok(job.includes(`uv run pytest -q --no-header ${gate}`));
  assert.ok(job.includes(`--ignore=${gate}`));
  assert.equal(job.split(gate).length - 1, 2);

  const lifecycleTest = read(`servers/fastapi/${gate}`);
  assert.match(lifecycleTest, /"uvicorn"[\s\S]{0,100}"api\.main:app"/u);
  assert.match(lifecycleTest, /workspace_crash_probe_worker\.py/u);
  const workerProbe = read(
    "servers/fastapi/tests/support/workspace_crash_probe_worker.py",
  );
  assert.match(workerProbe, /from services import workspace_operation_worker/u);
  assert.match(workerProbe, /workspace_operation_worker\.main\(\)/u);
  assert.match(lifecycleTest, /alembic/u);
});

test("Ruff is a required locked Python lint gate on both CI providers", () => {
  const workflow = read(".github/workflows/test-all.yml");
  const githubFastApiJob = workflowJob(workflow, "test-fastapi");
  const pipeline = read("bitbucket-pipelines.yml");
  const pyproject = read("servers/fastapi/pyproject.toml");
  const lockfile = read("servers/fastapi/uv.lock");

  assert.match(pyproject, /"ruff>=0\.16\.2"/u);
  assert.match(pyproject, /select\s*=\s*\["E",\s*"F",\s*"B",\s*"ASYNC"\]/u);
  assert.doesNotMatch(pyproject, /^ignore\s*=/mu);
  assert.match(lockfile, /name = "ruff"\s*\nversion = "0\.16\.2"/u);
  assert.match(githubFastApiJob, /name: Run Ruff lint gate \(E, F, B, ASYNC\)/u);
  assert.match(githubFastApiJob, /uv run ruff check \./u);
  assert.match(
    pipeline,
    /uv sync --frozen --directory servers\/fastapi[\s\S]*uv run --directory servers\/fastapi ruff check \./u,
  );
  assert.match(
    pipeline,
    /pull-requests:\s*\n\s*"\*\*":\s*[\s\S]*\*workspace-chat-first-e2e/u,
  );
  assert.match(
    pipeline,
    /branches:\s*\n\s*main:\s*[\s\S]*\*workspace-chat-first-e2e/u,
  );
});

test("workspace E2E is an always-captured PR gate on Bitbucket", () => {
  const pipeline = read("bitbucket-pipelines.yml");

  assert.match(pipeline, /pull-requests:\s*\n\s*"\*\*":/u);
  assert.match(pipeline, /branches:\s*\n\s*main:/u);
  assert.match(pipeline, /name: Workspace chat-first browser E2E/u);
  assert.match(pipeline, /max-time: 25/u);
  assert.match(
    pipeline,
    /export PATH="\$BITBUCKET_CLONE_DIR\/\.bitbucket-uv\/bin:\$PATH"/u,
  );
  assert.match(
    pipeline,
    /WORKSPACE_E2E_ARTIFACT_DIR=.*test-artifacts\/workspace-e2e/u,
  );
  assert.match(pipeline, /xvfb-run -a npm run test:workspace-e2e/u);
  assert.match(
    pipeline,
    /name: workspace-e2e-diagnostics\s*\n\s*type: scoped/u,
  );
  assert.match(pipeline, /test-artifacts\/workspace-e2e\/\*\*/u);
  assert.match(pipeline, /capture-on: always/u);
});

test("chat-first deployment configuration is required by both CI providers", () => {
  const rootPackage = readJson("package.json");
  const workflow = read(".github/workflows/test-all.yml");
  const githubNextJob = workflowJob(workflow, "test-nextjs");
  const pipeline = read("bitbucket-pipelines.yml");
  const nextCiRunner = read("servers/nextjs/scripts/run-ci-node-tests.mjs");

  assert.equal(
    rootPackage.scripts["test:chat-first-deployment-config"],
    "node scripts/check-chat-first-deployment-config.mjs",
  );
  assert.equal(
    rootPackage.scripts["test:file-size-ratchet"],
    "node scripts/check-file-size-ratchet.mjs",
  );
  assert.match(
    rootPackage.scripts["test:next-ci"],
    /npm run test:file-size-ratchet/u,
  );
  assert.match(
    rootPackage.scripts["test:next-ci"],
    /npm run test:chat-first-deployment-config/u,
  );
  assert.match(
    rootPackage.scripts["test:next-ci"],
    /scripts\/check-file-size-ratchet\.test\.mjs/u,
  );
  assert.match(nextCiRunner, /ROOTS = \[.*"tests".*\]/u);
  assert.ok(nextCiRunner.includes("const TEST_RE = /\\.test\\.(ts|tsx|mjs)$/"));
  const exclusions = nextCiRunner.slice(
    nextCiRunner.indexOf("const EXCLUDED"),
    nextCiRunner.indexOf("function walk"),
  );
  assert.doesNotMatch(
    exclusions,
    /tests\//u,
    "the explicit Next.js Node-test directory must not be excluded from CI discovery",
  );
  for (const testFile of [
    "element-insert-shapes.test.mjs",
    "math-insert-elements.test.mjs",
    "settings-access.test.mjs",
    "template-v2-json-to-html.test.mjs",
    "workspace-contract.test.mjs",
  ]) {
    assert.ok(
      read(`servers/nextjs/tests/${testFile}`).length > 0,
      `${testFile} must remain discoverable by the CI runner`,
    );
  }
  assert.match(githubNextJob, /npm run test:next-ci/u);
  assert.match(
    pipeline,
    /name: Run shared Next\.js CI Node suite[\s\S]*npm ci --prefix servers\/nextjs[\s\S]*npm run test:next-ci/u,
  );
  assert.match(
    pipeline,
    /pull-requests:\s*\n\s*"\*\*":\s*\n\s*- step: \*nextjs-ci-node-contracts/u,
  );
  assert.match(
    pipeline,
    /branches:\s*\n\s*main:\s*\n\s*- step: \*nextjs-ci-node-contracts/u,
  );
});

test("the E2E runner bounds Cypress, traps termination, and preserves replayable logs", () => {
  const runner = read("servers/nextjs/scripts/run-workspace-e2e.mjs");

  assert.match(runner, /runnerLockDirectory.*workspace-e2e\.lock/u);
  assert.match(runner, /async function acquireRunnerLock\(\)/u);
  assert.match(runner, /process\.kill\(-processGroupId, 0\)/u);
  assert.match(runner, /async function releaseRunnerLock\(\)/u);
  assert.match(runner, /project-files\.snapshot\.json/u);
  assert.match(runner, /createHash\("sha256"\)[\s\S]*validateDurableSnapshot/u);
  assert.match(runner, /RUNNER_OWNER_FILE_PREFIX = "owner\."/u);
  assert.match(
    runner,
    /RUNNER_RECOVERY_CLAIM_FILE_PREFIX = "recovery\.claim\."/u,
  );
  assert.match(
    runner,
    /const source = runnerOwnerFile\(lockDirectory, observedOwner\.token\);[\s\S]{0,220}await rename\(source, destination\)/u,
  );
  assert.match(
    runner,
    /await assertRecoveryOwnership\(recovery\);[\s\S]{0,120}await terminateRecordedChildProcesses\(recovery\.staleOwner\.token\)/u,
  );
  assert.equal(
    runner.match(/await assertRecoveryOwnership\(recovery\);/gu)?.length,
    3,
  );
  assert.match(
    runner,
    /await rename\(runnerLockDirectory, recoveredLockDirectory\);[\s\S]{0,500}await rm\(recoveredLockDirectory/u,
  );
  assert.match(
    runner,
    /recordChildSpawnIntent\(record\);[\s\S]{0,120}activeChildRecords\.add\(record\);[\s\S]{0,500}recordPendingChildProcess\(record\);[\s\S]{0,160}captureSpawnedProcessIdentity\(child, name\)/u,
  );
  assert.match(
    runner,
    /recordChildSpawnIntent\(record\);[\s\S]{0,240}const child = spawn\(spawnSpec\.command, spawnSpec\.args, spawnSpec\.options\)/u,
  );
  assert.match(
    runner,
    /async function spawnTrackedProcess[\s\S]{0,420}throwIfInterrupted\(\);[\s\S]{0,420}recordChildSpawnIntent\(record\)/u,
  );
  assert.match(
    runner,
    /catch \(registrationError\) \{[\s\S]{0,700}captureSpawnedProcessIdentity\([\s\S]{0,500}settleChildRegistration\(record\);[\s\S]{0,180}await terminateTrackedProcessTreeOnce\(record\)/u,
  );
  assert.match(
    runner,
    /child\.once\("close"[\s\S]{0,180}tryFinalizeNaturallyExitedChild\(record\)/u,
  );
  assert.match(
    runner,
    /tryFinalizeNaturallyExitedChild\(record\)[\s\S]{0,380}trackedTreeHasPotentialMembers\(record\)[\s\S]{0,100}removeTrackedChildMetadata\(record\)/u,
  );
  assert.match(
    runner,
    /processIdentity[\s\S]{0,160}expectedExecutable[\s\S]*terminateRecordedPidTree\(metadata\)/u,
  );
  assert.match(
    runner,
    /Older or partially written owner records[\s\S]{0,160}return Boolean\(currentIdentity\)/u,
  );
  assert.match(
    runner,
    /legacy or torn recovery-owner record[\s\S]{0,180}return Boolean\(currentIdentity\)/u,
  );
  assert.match(
    runner,
    /identityState: "spawn_intent"[\s\S]*identityState: "pending"[\s\S]*identityState: "verified"[\s\S]*metadata\.identityState !== "verified"/u,
  );
  assert.match(runner, /readFile\(`\/proc\/\$\{pid\}\/stat`/u);
  assert.match(runner, /CreationFileTime[\s\S]{0,160}ExecutablePath/u);
  assert.match(
    runner,
    /CreationFileTime = \[Convert\]::ToString\([\s\S]{0,160}StartTime\.ToUniversalTime\(\)\.ToFileTimeUtc/u,
  );
  assert.match(runner, /process\.kill\(-processGroupId, "SIGTERM"\)/u);
  assert.match(
    runner,
    /process\.platform !== "win32" && Number\(record\.processGroupId\) !== pid/u,
  );
  assert.doesNotMatch(runner, /windowsDescendants/u);
  assert.doesNotMatch(runner, /taskkill(?:\.exe)?/iu);
  assert.match(
    runner,
    /JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE[\s\S]*CreateProcessW\(applicationName[\s\S]*CREATE_SUSPENDED[\s\S]*AssignProcessToJobObject[\s\S]*ResumeThread/u,
  );
  assert.match(
    runner,
    /parent = OpenProcess\(SYNCHRONIZE \| PROCESS_QUERY_LIMITED_INFORMATION[\s\S]{0,300}VerifyParent\(parent/u,
  );
  assert.match(
    runner,
    /Win32\(GetProcessTimes\(parent[\s\S]{0,700}Win32\(QueryFullProcessImageName\(parent/u,
  );
  assert.match(
    runner,
    /Recorded Windows PIDs are never signaled; cleanup requires a retained Job Object supervisor handle/u,
  );
  assert.match(
    runner,
    /await terminateRetainedWindowsJobSupervisor\(trackedRecord\)/u,
  );
  assert.match(
    runner,
    /terminateProcessRecord\(metadata\);[\s\S]{0,120}trackedTreeHasPotentialMembers\(metadata\)/u,
  );
  assert.match(
    runner,
    /await stopRegisteredChildRecords\(activeChildRecords,[\s\S]{0,260}await terminateTrackedProcessTreeOnce\(record\)[\s\S]{0,260}await removeTrackedChildMetadata\(record\)/u,
  );
  assert.match(
    runner,
    /await record\.registrationPromise;[\s\S]{0,80}await stopRecord\(record\)/u,
  );
  assert.match(runner, /RECOVERY_REQUIRED\.json/u);
  assert.match(
    runner,
    /await restoreProjectFiles\(\)[\s\S]*if \(safetyErrors\.length > 0\)[\s\S]*await releaseRunnerLock\(\)/u,
  );
  assert.match(
    runner,
    /Received \$\{signal\}[\s\S]{0,420}\.then\(\(\) => cleanup\(\)\)/u,
  );
  assert.match(runner, /CYPRESS_TIMEOUT_MS.*240_000/u);
  assert.match(
    runner,
    /waitForExitWithTimeout\(\s*cypressProcess,\s*"Cypress route suite",?\s*\)/u,
  );
  assert.match(
    runner,
    /waitForExitWithTimeout\(\s*cypressProcess,\s*"Cypress live-backend suite",?\s*\)/u,
  );
  assert.match(runner, /for \(const signal of \["SIGINT", "SIGTERM"\]\)/u);
  assert.match(
    runner,
    /finally \{[\s\S]*runnerLockToken[\s\S]*await cleanup\(\);[\s\S]*\}/u,
  );
  assert.match(runner, /failure-diagnostics\.json/u);
  for (const logName of [
    "next-server",
    "fastapi-live-harness",
    "cypress-route",
    "cypress-live-backend",
  ]) {
    assert.match(runner, new RegExp(`"${logName}"`, "u"));
  }
  assert.match(
    runner,
    /screenshotsFolder=.*videosFolder=.*video=true.*trashAssetsBeforeRuns=false/u,
  );
  assert.match(runner, /artifactDirectory,\s*"cypress"/u);
  assert.match(runner, /run-metadata\.json/u);
  assert.match(runner, /destination\.on\("error"/u);
  assert.match(runner, /await forwarding\.ready/u);
  assert.match(runner, /project-files\.runner-generated\.json/u);
  assert.match(runner, /afterCompareBeforeSwap/u);
  assert.match(
    runner,
    /writeDurableFileSync\(snapshot\.file, snapshot\.contents, "wx"\)/u,
  );
});

test("artifact log open failures are awaited and never become uncaught emitter errors", async (t) => {
  const temporaryDirectory = await mkdtemp(
    path.join(os.tmpdir(), "workspace-e2e-log-contract-"),
  );
  t.after(() => rm(temporaryDirectory, { recursive: true, force: true }));
  const invalidLogFile = path.join(temporaryDirectory, "is-a-directory.log");
  await mkdir(invalidLogFile);
  const { forwardChildOutputToLog } = await import(
    new URL("../servers/nextjs/scripts/run-workspace-e2e.mjs", import.meta.url)
  );
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  const failures = [];
  const forwarding = forwardChildOutputToLog(child, invalidLogFile, {
    name: "fault-injected-child",
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    onFailure: (error) => failures.push(error),
  });
  await assert.rejects(forwarding.ready, /Could not persist.*artifact log/u);
  child.emit("close");
  await forwarding.completion;
  assert.equal(failures.length, 1);
});

test("signal cleanup waits for child registration before stopping the retained record", async () => {
  const { stopRegisteredChildRecords } = await import(
    new URL("../servers/nextjs/scripts/run-workspace-e2e.mjs", import.meta.url)
  );
  let finishRegistration;
  const record = {
    name: "registration-race",
    registrationPromise: new Promise((resolve) => {
      finishRegistration = resolve;
    }),
  };
  const stopped = [];
  const cleanup = stopRegisteredChildRecords([record], async (registered) => {
    stopped.push(registered.name);
  });

  await Promise.resolve();
  assert.deepEqual(stopped, [], "cleanup must not overtake durable registration");
  finishRegistration();
  await cleanup;
  assert.deepEqual(stopped, ["registration-race"]);
});

test("Windows cleanup uses a retained supervisor handle and stale recovery never signals a recycled PID", async () => {
  const {
    terminateRetainedWindowsJobSupervisor,
    windowsSupervisorRecoveryDisposition,
  } = await import(
    new URL("../servers/nextjs/scripts/run-workspace-e2e.mjs", import.meta.url)
  );
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  let retainedHandleKills = 0;
  child.kill = () => {
    retainedHandleKills += 1;
    queueMicrotask(() => {
      child.exitCode = 143;
      child.emit("exit", 143, null);
    });
    return true;
  };
  await terminateRetainedWindowsJobSupervisor({
    child,
    closed: false,
    terminationMode: "windows_job_supervisor_v1",
  });
  assert.equal(retainedHandleKills, 1);

  const exactIdentity = {
    platform: "win32",
    birthId: "133700000000000000",
    executable: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
  };
  const verifiedMetadata = {
    schemaVersion: 3,
    identityState: "verified",
    processIdentity: exactIdentity,
    expectedExecutable: exactIdentity.executable,
    protocolVersion: 1,
    terminationMode: "windows_job_supervisor_v1",
  };
  assert.deepEqual(
    windowsSupervisorRecoveryDisposition(verifiedMetadata, exactIdentity),
    { recoverable: false, reason: "exact_supervisor_still_live" },
    "an exact live supervisor must fail closed",
  );
  assert.deepEqual(
    windowsSupervisorRecoveryDisposition(verifiedMetadata, {
      ...exactIdentity,
      birthId: "133700000000000999",
    }),
    { recoverable: true, reason: "supervisor_exited_or_pid_replaced" },
    "a recycled PID is proof the original retained job handle has closed",
  );
  assert.deepEqual(
    windowsSupervisorRecoveryDisposition(verifiedMetadata, undefined),
    { recoverable: true, reason: "supervisor_exited_or_pid_replaced" },
  );
  assert.deepEqual(
    windowsSupervisorRecoveryDisposition(
      {
        ...verifiedMetadata,
        identityState: "pending",
        processIdentity: undefined,
        expectedExecutable: undefined,
      },
      exactIdentity,
    ),
    { recoverable: false, reason: "pending_pid_occupied" },
    "an occupied PID without a durable birth identity is unverifiable",
  );
  assert.throws(
    () =>
      windowsSupervisorRecoveryDisposition(
        { ...verifiedMetadata, schemaVersion: 2 },
        undefined,
      ),
    /refusing automatic recovery/u,
    "legacy PID-only metadata must fail closed",
  );
});

test("the Windows supervisor spawn contract binds parent identity before a suspended child can run", async () => {
  const { createWindowsJobSupervisorSpawnSpec } = await import(
    new URL("../servers/nextjs/scripts/run-workspace-e2e.mjs", import.meta.url)
  );
  const parentIdentity = {
    platform: "win32",
    birthId: "133700000000000000",
    executable: process.execPath,
  };
  const spec = createWindowsJobSupervisorSpawnSpec(
    process.execPath,
    ["script with spaces.mjs", 'quote"inside', "trailing slash \\"],
    {
      cwd: path.resolve("servers/nextjs"),
      env: { CONTRACT_SENTINEL: "retained" },
      parentPid: 4242,
      parentIdentity,
    },
  );
  const encoded = spec.options.env.PRESENTON_WORKSPACE_JOB_SUPERVISOR_SPEC_BASE64;
  const decoded = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));

  assert.equal(spec.command, "powershell.exe");
  assert.equal(spec.options.detached, false);
  assert.equal(spec.terminationMode, "windows_job_supervisor_v1");
  assert.equal(decoded.applicationName, process.execPath);
  assert.equal(decoded.parentPid, 4242);
  assert.equal(decoded.parentBirthId, parentIdentity.birthId);
  assert.equal(decoded.parentExecutable, process.execPath);
  assert.match(decoded.commandLine, /"script with spaces\.mjs"/u);
  assert.match(decoded.commandLine, /"quote\\"inside"/u);
  assert.equal(decoded.commandLine.endsWith('"trailing slash \\\\"'), true);
});

test("the exact generated-file run plan is durable before the first child spawn", () => {
  const runner = read("servers/nextjs/scripts/run-workspace-e2e.mjs");
  const runPlan = runner.indexOf(
    "await persistProjectFileRunPlan(relativeRunDirectory);",
  );
  const generatedPlan = runner.indexOf(
    "await persistPlannedRunnerGeneratedProjectFiles(",
  );
  const firstChildSpawn = runner.indexOf("nextProcess = await spawnNode(");

  assert.ok(runPlan >= 0);
  assert.ok(generatedPlan > runPlan);
  assert.ok(firstChildSpawn > generatedPlan);
  assert.match(
    runner,
    /schemaVersion: 2,[\s\S]{0,100}state: "planned"[\s\S]{0,300}candidates: plannedRunnerGeneratedProjectFileCandidates/u,
  );
  assert.match(
    runner,
    /contentsBase64: contents\.toString\("base64"\)[\s\S]{0,100}createHash\("sha256"\)/u,
  );
});

test("runner-generated project files restore by CAS and preserve a concurrent editor save", async (t) => {
  const temporaryDirectory = await mkdtemp(
    path.join(os.tmpdir(), "workspace-e2e-project-cas-"),
  );
  t.after(() => rm(temporaryDirectory, { recursive: true, force: true }));
  const lockDirectory = path.join(temporaryDirectory, "lock");
  await mkdir(lockDirectory);
  const {
    isExpectedRunnerGeneratedProjectFile,
    plannedRunnerGeneratedProjectFileCandidates,
    restoreProjectFileSnapshot,
  } = await import(
    new URL(
      "../servers/nextjs/scripts/run-workspace-e2e.mjs",
      import.meta.url,
    )
  );
  const relativeRunDirectory = ".next-workspace-e2e/run-123-456";
  const original = Buffer.from(
    `${JSON.stringify({ compilerOptions: { strict: true }, include: ["next-env.d.ts"] }, null, 2)}\n`,
  );
  const generated = plannedRunnerGeneratedProjectFileCandidates(
    "tsconfig.json",
    original,
    relativeRunDirectory,
  ).find((candidate) => !candidate.equals(original));
  assert.ok(generated, "the runner must predeclare the exact Next tsconfig bytes");
  assert.equal(
    isExpectedRunnerGeneratedProjectFile(
      "tsconfig.json",
      original,
      generated,
      relativeRunDirectory,
    ),
    true,
  );
  assert.equal(
    isExpectedRunnerGeneratedProjectFile(
      "tsconfig.json",
      original,
      Buffer.from(
        `${JSON.stringify({ compilerOptions: { strict: false }, include: ["next-env.d.ts"] }, null, 2)}\n`,
      ),
      relativeRunDirectory,
    ),
    false,
  );

  const generatedObject = JSON.parse(generated.toString("utf8"));
  const formattingOnlyRewrite = Buffer.from(
    `${JSON.stringify(generatedObject, null, 4)}${os.EOL}`,
  );
  assert.equal(
    isExpectedRunnerGeneratedProjectFile(
      "tsconfig.json",
      original,
      formattingOnlyRewrite,
      relativeRunDirectory,
    ),
    false,
    "semantic equality cannot authorize overwriting marker-less user formatting",
  );
  const userAndRunnerMix = Buffer.from(
    `${JSON.stringify(
      {
        ...generatedObject,
        include: [...generatedObject.include, "user-authored/**/*.ts"],
      },
      null,
      2,
    )}${os.EOL}`,
  );
  assert.equal(
    isExpectedRunnerGeneratedProjectFile(
      "tsconfig.json",
      original,
      userAndRunnerMix,
      relativeRunDirectory,
    ),
    false,
    "a user edit mixed with runner includes must never be classified as generated",
  );

  const nextEnvOriginal = Buffer.from(
    [
      '/// <reference types="next" />',
      '/// <reference types="next/image-types/global" />',
      "",
      "// NOTE: This file should not be edited",
      "// see https://nextjs.org/docs/app/api-reference/config/typescript for more information.",
      "",
    ].join(os.EOL),
  );
  const nextEnvGenerated = plannedRunnerGeneratedProjectFileCandidates(
    "next-env.d.ts",
    nextEnvOriginal,
    relativeRunDirectory,
  ).find((candidate) => !candidate.equals(nextEnvOriginal));
  assert.ok(nextEnvGenerated, "the runner must predeclare exact next-env bytes");
  assert.equal(
    isExpectedRunnerGeneratedProjectFile(
      "next-env.d.ts",
      nextEnvOriginal,
      nextEnvGenerated,
      relativeRunDirectory,
    ),
    true,
  );
  const alternateEol = os.EOL === "\r\n" ? "\n" : "\r\n";
  const nextEnvEolRewrite = Buffer.from(
    nextEnvGenerated.toString("utf8").replaceAll(os.EOL, alternateEol),
  );
  assert.notDeepEqual(nextEnvEolRewrite, nextEnvGenerated);
  assert.equal(
    isExpectedRunnerGeneratedProjectFile(
      "next-env.d.ts",
      nextEnvOriginal,
      nextEnvEolRewrite,
      relativeRunDirectory,
    ),
    false,
    "line-ending-only changes are not an exact runner-generated snapshot",
  );
  const nextEnvUserMix = Buffer.concat([
    nextEnvGenerated,
    Buffer.from(`/// <reference types="user-owned" />${os.EOL}`),
  ]);
  assert.equal(
    isExpectedRunnerGeneratedProjectFile(
      "next-env.d.ts",
      nextEnvOriginal,
      nextEnvUserMix,
      relativeRunDirectory,
    ),
    false,
    "runner markers plus user bytes cannot authorize restore",
  );

  const ownerToken = randomUUID();
  const normalFile = path.join(temporaryDirectory, "normal-tsconfig.json");
  await writeFile(normalFile, generated);
  await restoreProjectFileSnapshot(
    {
      file: normalFile,
      contents: original,
      runnerGeneratedContents: generated,
    },
    { lockDirectory, ownerToken },
  );
  assert.deepEqual(await readFile(normalFile), original);

  const concurrentFile = path.join(
    temporaryDirectory,
    "concurrent-tsconfig.json",
  );
  const userSave = Buffer.from("user-save-must-survive\n");
  await writeFile(concurrentFile, generated);
  await assert.rejects(
    restoreProjectFileSnapshot(
      {
        file: concurrentFile,
        contents: original,
        runnerGeneratedContents: generated,
      },
      {
        lockDirectory,
        ownerToken,
        afterCompareBeforeSwap: () => writeFile(concurrentFile, userSave),
      },
    ),
    (error) => error?.code === "EWORKSPACE_CONCURRENT_CHANGE",
  );
  assert.deepEqual(await readFile(concurrentFile), userSave);
});

test("a delayed stale-lock reaper cannot claim or delete a replacement owner", async (t) => {
  const lockDirectory = await mkdtemp(
    path.join(os.tmpdir(), "workspace-e2e-lock-contract-"),
  );
  t.after(() => rm(lockDirectory, { recursive: true, force: true }));
  const {
    claimRunnerOwnerByToken,
    processIdentitiesMatch,
    readProcessIdentity,
    runnerOwnerFileName,
  } = await import(
    new URL("../servers/nextjs/scripts/run-workspace-e2e.mjs", import.meta.url)
  );

  const staleOwner = { pid: 1001, token: randomUUID() };
  const firstClaimToken = randomUUID();
  await writeFile(
    path.join(lockDirectory, runnerOwnerFileName(staleOwner.token)),
    JSON.stringify(staleOwner),
  );
  await claimRunnerOwnerByToken(lockDirectory, staleOwner, firstClaimToken);
  const claimedEntries = await readdir(lockDirectory);
  assert.deepEqual(claimedEntries, [`recovery.claim.${firstClaimToken}.json`]);

  await rm(lockDirectory, { recursive: true });
  await mkdir(lockDirectory);
  const replacementOwner = { pid: 2002, token: randomUUID() };
  const replacementOwnerFile = path.join(
    lockDirectory,
    runnerOwnerFileName(replacementOwner.token),
  );
  await writeFile(replacementOwnerFile, JSON.stringify(replacementOwner));

  await assert.rejects(
    claimRunnerOwnerByToken(lockDirectory, staleOwner, randomUUID()),
    (error) => error?.code === "ENOENT",
  );
  assert.deepEqual(
    JSON.parse(await readFile(replacementOwnerFile, "utf8")),
    replacementOwner,
  );

  const identity = {
    platform: process.platform,
    birthId: "boot-or-creation-time:123",
    executable: process.execPath,
  };
  assert.equal(processIdentitiesMatch(identity, { ...identity }), true);
  assert.equal(
    processIdentitiesMatch(identity, { ...identity, birthId: "reused:456" }),
    false,
  );
  assert.equal(
    processIdentitiesMatch(identity, {
      ...identity,
      executable: `${process.execPath}.unexpected`,
    }),
    false,
  );

  const observedIdentity = await readProcessIdentity(process.pid);
  assert.ok(observedIdentity?.birthId);
  assert.ok(observedIdentity?.executable);
  assert.equal(
    processIdentitiesMatch(observedIdentity, { ...observedIdentity }),
    true,
  );
});

test("the local default profile runs E2E with isolated pytest and Windows diagnostics", () => {
  const localRunner = read("scripts/test-local.mjs");
  const nextPackage = readJson("servers/nextjs/package.json");
  const rootPackage = readJson("package.json");

  assert.equal(
    nextPackage.scripts["test:workspace-e2e"],
    "node scripts/run-workspace-e2e.mjs",
  );
  assert.match(
    rootPackage.scripts["test:next-ci"],
    /scripts\/required-ci-contract\.test\.mjs/u,
  );
  assert.match(
    localRunner,
    /name: "Workspace chat-first browser \+ FastAPI SQLite E2E"/u,
  );
  assert.match(localRunner, /args: workspaceE2eArgs/u);
  assert.doesNotMatch(
    localRunner,
    /selected:\s*requested\("--with-[^"]+"\)[\s\S]{0,160}Workspace chat-first/u,
  );
  assert.match(localRunner, /PYTEST_DEBUG_TEMPROOT/u);
  assert.match(localRunner, /--basetemp/u);
  assert.match(
    localRunner,
    /name: "Full FastAPI SQLite suite"[\s\S]{0,300}"pytest"/u,
  );
  assert.doesNotMatch(
    localRunner,
    /--ignore[=",\s]+tests[\\/]integration[\\/]test_workspace_production_lifecycle_gate\.py/u,
  );
  assert.match(localRunner, /maxRetries: isWindows \? 12 : 3/u);
  assert.match(localRunner, /EPERM.*EACCES.*EBUSY/u);
  assert.match(localRunner, /Preserving failed local run artifacts/u);
  for (const requiredComponentSpec of [
    "Chat.cy.tsx",
    "WorkspaceChatPanel.cy.tsx",
    "SharePopover.cy.tsx",
  ]) {
    assert.match(
      localRunner,
      new RegExp(requiredComponentSpec.replace(".", "\\."), "u"),
    );
  }
});

test("the required component gate covers workspace stream and rollback UI", () => {
  const workflow = read(".github/workflows/test-all.yml");
  const job = workflowJob(workflow, "test-nextjs");

  for (const requiredComponentSpec of [
    "Chat.cy.tsx",
    "WorkspaceChatPanel.cy.tsx",
    "SharePopover.cy.tsx",
  ]) {
    assert.match(
      job,
      new RegExp(requiredComponentSpec.replace(".", "\\."), "u"),
    );
  }
});

test("Windows PR packaging preflights symlink policy and validates a link-free resource", () => {
  const workflow = read(".github/workflows/g4-pptx-roundtrip.yml");
  const job = workflowJob(workflow, "windows-release-gate");
  const builder = read("electron/build_nextjs_resources.js");
  const copy = read("electron/scripts/standalone-copy.cjs");
  const buildFallback = read(
    "electron/scripts/next-standalone-symlink-fallback.cjs",
  );

  assert.match(workflow, /push:\s*\n\s*branches: \[main\]/u);
  assert.match(workflow, /pull_request:\s*\n\s*branches: \[main\]/u);
  assert.match(job, /runs-on: windows-latest/u);
  assert.match(job, /npm run test:standalone-copy/u);
  assert.match(job, /npm run build:nextjs/u);
  assert.match(job, /validateLinkFreeTree\('resources\/nextjs'\)/u);
  assert.match(builder, /preflightWindowsSymlinkFallback\(\)/u);
  assert.match(builder, /--require=.*standaloneSymlinkFallback/u);
  assert.match(builder, /PRESENTON_NEXT_STANDALONE_LINK_FALLBACK: "1"/u);
  assert.match(builder, /PRESENTON_NEXT_STANDALONE_ROOT: standaloneDir/u);
  assert.match(
    builder,
    /PRESENTON_NEXT_TRACE_ROOT: path\.resolve\(nextjsDir, "\.\.", "\.\."\)/u,
  );
  assert.match(builder, /copyTreeSafe\([\s\S]*materializeLinks: true/u);
  assert.match(builder, /validateLinkFreeTree\(tempOutDir\)/u);
  assert.match(
    copy,
    /error\?\.code === "EPERM" \|\| error\?\.code === "EACCES"/u,
  );
  assert.match(copy, /privilege-free materialized-copy fallback/u);
  assert.match(
    buildFallback,
    /error\?\.code !== "EPERM" && error\?\.code !== "EACCES"/u,
  );
  assert.match(
    buildFallback,
    /originalSymlink\(realTarget, destination, "junction"\)/u,
  );
  assert.match(buildFallback, /copyFile\(realTarget, destination\)/u);
  assert.match(buildFallback, /!isWithin\(resolvedRoot, destination\)/u);
  assert.match(buildFallback, /!isWithin\(resolvedTracingRoot, realTarget\)/u);
});

test("the standard Next.js build enables the bounded Windows standalone link fallback", () => {
  const nextPackage = readJson("servers/nextjs/package.json");
  const buildRunner = read("servers/nextjs/scripts/run-next-build.mjs");

  assert.equal(nextPackage.scripts.build, "node scripts/run-next-build.mjs");
  assert.match(buildRunner, /process\.platform === "win32"/);
  assert.match(buildRunner, /PRESENTON_NEXT_STANDALONE_LINK_FALLBACK: "1"/);
  assert.match(buildRunner, /PRESENTON_NEXT_STANDALONE_ROOT/);
  assert.match(buildRunner, /PRESENTON_NEXT_TRACE_ROOT/);
  assert.match(buildRunner, /shell: false/);
});

test("the Next.js build reads the repository-root .env so public flags are baked in", () => {
  // `NEXT_PUBLIC_*` values are inlined into the client bundle at build time, and
  // Next.js only reads `.env` from its own project directory.  A build that
  // cannot see the repository-root file ships the chat-first workbench flag as
  // `undefined`, which silently falls the workspace back to the legacy popup UI.
  const buildRunner = read("servers/nextjs/scripts/run-next-build.mjs");

  assert.match(buildRunner, /repositoryDirectory, "\.env"/u);
  assert.match(buildRunner, /repositoryEnvironment\(\)/u);
  // The repository file must not override an explicit environment value.
  assert.match(buildRunner, /if \(process\.env\[name\] !== undefined\) continue;/u);
  assert.match(
    buildRunner,
    /childEnvironment = \{ \.\.\.repositoryEnvironment\(\), \.\.\.process\.env \}/u,
  );
});

test("the required route gate enforces axe and bounded approval announcements", () => {
  const nextPackage = readJson("servers/nextjs/package.json");
  const runner = read("servers/nextjs/scripts/run-workspace-e2e.mjs");
  const spec = read("servers/nextjs/cypress/e2e/workspaceChatFirstRoute.cy.ts");

  assert.equal(nextPackage.devDependencies["axe-core"], "4.13.0");
  assert.match(runner, /cypress\/e2e\/workspaceChatFirstRoute\.cy\.ts/u);
  assert.match(spec, /node_modules\/axe-core\/axe\.min\.js/u);
  assert.match(spec, /window\.Function\(source\)\(\)/u);
  assert.match(spec, /axe-core did not attach to the application window/u);
  assert.match(spec, /pageAxe\.run\(window\.document\)/u);
  assert.match(
    spec,
    /operation-approval-alert[\s\S]{0,180}role", "alert"[\s\S]{0,180}aria-live", "assertive"/u,
  );
  assert.match(
    spec,
    /\[role="status"\]\[aria-live="polite"\][\s\S]{0,80}have\.length", 1/u,
  );
  assert.match(spec, /results\.violations[\s\S]{0,180}have\.length\(0\)/u);
});

test("the route gate proves an authoritative kill switch wins across real browsing contexts", () => {
  const spec = read("servers/nextjs/cypress/e2e/workspaceChatFirstRoute.cy.ts");
  const scenario = spec.slice(
    spec.indexOf(
      "revalidates a server rollback across two real browsing contexts",
    ),
    spec.indexOf(
      "keeps the bootstrap request as the first durable chat message",
    ),
  );

  assert.ok(scenario.length > 0, "server rollback scenario is missing");
  assert.match(scenario, /capabilityResponse\s*=\s*\{[\s\S]*enabled:\s*false/u);
  assert.match(scenario, /reason:\s*"chat_first_kill_switch_enabled"/u);
  assert.match(
    scenario,
    /createElement\("iframe"\)[\s\S]*workspace-secondary-context/u,
  );
  assert.match(
    scenario,
    /secondaryWorkspaceWindow[\s\S]*workspace-command-form/u,
  );
  assert.match(scenario, /new window\.Event\("focus"\)/u);
  assert.match(scenario, /new window\.Event\("pageshow"\)/u);
  assert.match(
    scenario,
    /requestsBeforeDisable\s*=\s*capabilityRequestCount[\s\S]*greaterThan\(requestsBeforeDisable\)/u,
  );
  assert.match(
    scenario,
    /requestsBeforeEnable\s*=\s*capabilityRequestCount[\s\S]*greaterThan\(requestsBeforeEnable\)/u,
  );
  assert.match(scenario, /workspace-command-form[\s\S]{0,80}not\.exist/u);
  assert.match(scenario, /share-popover-trigger[\s\S]{0,80}be\.visible/u);
  assert.match(
    scenario,
    /presentation-title-edit[\s\S]{0,80}not\.be\.disabled/u,
  );
  assert.match(scenario, /capabilityResponse\s*=\s*enabledCapability/u);
  assert.match(
    scenario,
    /waitForWindowText\(secondaryWorkspaceWindow as Window, firstUserMessage\.content\)/u,
  );
});

test("presentation creation and outline generation retain one stable deck identity", () => {
  const uploadState = read(
    "servers/nextjs/app/(presentation-generator)/upload/components/upload-page/useUploadPageState.ts",
  );
  const outline = read(
    "servers/nextjs/app/(presentation-generator)/outline/hooks/usePresentationGeneration.ts",
  );

  assert.match(
    uploadState,
    /const \[pendingPresentationId\] = useState\(\(\) => crypto\.randomUUID\(\)\)/u,
  );
  assert.equal(
    uploadState.match(/const presentationId = pendingPresentationId;/gu)?.length,
    2,
    "document and prompt-only creates must reuse the stable deck id",
  );
  assert.equal(
    uploadState.match(/presentation_id: presentationId,/gu)?.length,
    2,
    "both create admissions must send that same id",
  );
  assert.equal(
    uploadState.match(
      /idempotency_key: `presentation-create:\$\{presentationId\}`/gu,
    )?.length,
    2,
    "both create admissions must derive idempotency from the stable deck id",
  );
  assert.equal(
    outline.match(/presentation_id: presentationId,/gu)?.length,
    4,
    "every outline generation admission must retain the stable deck id",
  );
});

test("every shell Logo image used during startup is prioritized for LCP", () => {
  for (const filePath of [
    "servers/nextjs/app/ConfigurationInitializer.tsx",
    "servers/nextjs/components/Auth/AuthGate.tsx",
    "servers/nextjs/components/OnBoarding/OnBoardingSlidebar.tsx",
  ]) {
    const source = read(filePath);
    let cursor = 0;
    let count = 0;
    while ((cursor = source.indexOf('src="/Logo.png"', cursor)) !== -1) {
      count += 1;
      assert.match(
        source.slice(cursor, cursor + 320),
        /priority/u,
        `${filePath} Logo must be priority`,
      );
      cursor += 1;
    }
    assert.ok(count > 0, `${filePath} must contain its startup Logo`);
  }
});
