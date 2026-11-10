import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import { readFileSync, readdirSync } from "node:fs";
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

test("Ruff is a required locked Python lint gate on Bitbucket", () => {
  const pipeline = read("bitbucket-pipelines.yml");
  const pyproject = read("servers/fastapi/pyproject.toml");
  const lockfile = read("servers/fastapi/uv.lock");

  assert.match(pyproject, /"ruff>=0\.16\.2"/u);
  assert.match(pyproject, /select\s*=\s*\["E",\s*"F",\s*"B",\s*"ASYNC"\]/u);
  assert.match(pyproject, /^ignore\s*=\s*\["E501"\]/mu);
  assert.match(lockfile, /name = "ruff"\s*\nversion = "0\.16\.2"/u);
  assert.match(
    pipeline,
    /uv sync --frozen --directory servers\/fastapi[\s\S]*uv run --directory servers\/fastapi ruff check \./u,
  );
  assert.match(
    pipeline,
    /uv run --directory servers\/fastapi ruff check \.\s*\n\s*-\s*\.bitbucket-uv\/bin\/uv run --directory servers\/fastapi python scripts\/check_authored_style_samples\.py/u,
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

test("the svg unit subset is a deterministic, parallel PR and main gate on Bitbucket", () => {
  const pipeline = read("bitbucket-pipelines.yml");
  const pyproject = read("servers/fastapi/pyproject.toml");
  const lockfile = read("servers/fastapi/uv.lock");
  const subsetList = read("servers/fastapi/tests/svg_unit_subset.txt")
    .split("\n")
    .filter((line) => line.trim() !== "");

  assert.match(pipeline, /name: FastAPI svg unit subset/u);
  assert.match(
    pipeline,
    /pull-requests:\s*\n\s*"\*\*":\s*[\s\S]*\*fastapi-svg-unit-subset[\s\S]*branches:/u,
  );
  assert.match(
    pipeline,
    /branches:\s*\n\s*main:\s*[\s\S]*\*fastapi-svg-unit-subset[\s\S]*custom:/u,
  );
  // The same environment the CLAUDE.md CI section prescribes for pytest.
  assert.match(pipeline, /export DISABLE_IMAGE_GENERATION=true/u);
  assert.match(pipeline, /export DATABASE_URL=sqlite\+aiosqlite:\/\/\/\.\/test\.db/u);
  assert.match(pipeline, /export APP_DATA_DIRECTORY=\/tmp\/app_data/u);
  // The list must match its derivation rule before it is trusted, then it is the
  // only thing pytest is handed, so the step is deterministic.
  assert.match(
    pipeline,
    /uv run python scripts\/svg_unit_subset\.py --check\s*\n\s*-\s*\.\.\/\.\.\/\.bitbucket-uv\/bin\/uv run pytest \$\(cat tests\/svg_unit_subset\.txt\) -n 4 --dist loadfile/u,
  );
  assert.match(pipeline, /name: FastAPI svg unit subset\s*\n\s*size: 2x\s*\n\s*max-time: 30/u);
  // pytest-xdist is a locked dev dependency, not an ad-hoc install.
  assert.match(pyproject, /"pytest-xdist>=3\.8\.0"/u);
  assert.match(lockfile, /name = "pytest-xdist"\nversion = "3\.8\.0"/u);
  assert.doesNotMatch(pipeline, /pip install[^\n]*pytest-xdist/u);
  // The checked-in list is non-trivial, sorted, unique and made of existing unit files.
  assert.ok(subsetList.length > 400, `svg unit subset has ${subsetList.length} files`);
  assert.deepEqual(subsetList, [...subsetList].sort());
  assert.equal(new Set(subsetList).size, subsetList.length);
  for (const entry of subsetList) {
    assert.match(entry, /^tests\/unit\/test_[A-Za-z0-9_]+\.py$/u);
    assert.doesNotThrow(() => read(`servers/fastapi/${entry}`), entry);
  }
  for (const anchor of [
    "tests/unit/test_svg_generation_admission.py",
    "tests/unit/test_svg_export_deadline_precheck.py",
    "tests/unit/test_authored_canvas_defaults.py",
  ]) {
    assert.ok(subsetList.includes(anchor), `${anchor} must stay in the svg unit subset`);
  }
});

test("chat-first deployment configuration is required by the Bitbucket CI suite", () => {
  const rootPackage = readJson("package.json");
  const pipeline = read("bitbucket-pipelines.yml");
  const nextCiRunner = read("servers/nextjs/scripts/run-ci-node-tests.mjs");

  assert.equal(
    rootPackage.scripts["test:chat-first-deployment-config"],
    "node scripts/check-chat-first-deployment-config.mjs",
  );
  assert.match(
    rootPackage.scripts["test:next-ci"],
    /npm run test:chat-first-deployment-config/u,
  );
  assert.equal(
    rootPackage.scripts["check:release-sbom"],
    "node scripts/generate-release-sbom.mjs --check",
  );
  assert.equal(
    rootPackage.scripts["check:notice"],
    "uv run --directory servers/fastapi python ../../scripts/rebuild_notice_all.py --check",
  );
  assert.match(rootPackage.scripts["test:next-ci"], /npm run check:release-sbom/u);
  assert.match(rootPackage.scripts["test:next-ci"], /npm run check:notice/u);
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
    "settings-access.test.mjs",
    "workspace-contract.test.mjs",
  ]) {
    assert.ok(
      read(`servers/nextjs/tests/${testFile}`).length > 0,
      `${testFile} must remain discoverable by the CI runner`,
    );
  }
  assert.match(
    pipeline,
    /name: Run shared Next\.js CI Node suite[\s\S]*npm ci --prefix servers\/nextjs[\s\S]*npm run test:next-ci/u,
  );
  // test:next-ci runs check:notice and check:release-sbom, which shell out to
  // uv, so the step must provision python/uv and sync the locked environment
  // before it.
  assert.match(
    pipeline,
    /name: Run shared Next\.js CI Node suite[\s\S]*python3 -m venv \.bitbucket-uv[\s\S]*uv==0\.11\.17[\s\S]*uv sync --frozen --directory servers\/fastapi[\s\S]*export PATH="\$BITBUCKET_CLONE_DIR\/\.bitbucket-uv\/bin:\$PATH"[\s\S]*npm run test:next-ci/u,
  );
  // The shared Node suite must be in both required step lists; other required
  // steps (e.g. the authored release-readiness check) may precede it.
  assert.match(
    pipeline,
    /pull-requests:\s*\n\s*"\*\*":\s*\n(?:\s*- step: \*[\w-]+\r?\n)*?\s*- step: \*nextjs-ci-node-contracts/u,
  );
  assert.match(
    pipeline,
    /branches:\s*\n\s*main:\s*\n(?:\s*- step: \*[\w-]+\r?\n)*?\s*- step: \*nextjs-ci-node-contracts/u,
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
  assert.match(runner, /CYPRESS_TIMEOUT_MS.*600_000/u);
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

test("stale recovery requires persisted same-HEAD proof and refuses dirty snapshots or current files", async (t) => {
  const temporaryDirectory = await mkdtemp(
    path.join(os.tmpdir(), "workspace-e2e-git-clean-recovery-"),
  );
  t.after(() => rm(temporaryDirectory, { recursive: true, force: true }));
  const lockDirectory = path.join(temporaryDirectory, "lock");
  const projectDirectory = path.join(temporaryDirectory, "project");
  await mkdir(lockDirectory);
  await mkdir(projectDirectory);

  const git = (...args) =>
    spawnSync("git", args, {
      cwd: projectDirectory,
      encoding: "utf8",
      windowsHide: true,
    });
  assert.equal(git("init").status, 0);
  assert.equal(git("config", "user.name", "Workspace E2E Test").status, 0);
  assert.equal(
    git("config", "user.email", "workspace-e2e@example.invalid").status,
    0,
  );

  const projectFile = path.join(projectDirectory, "tsconfig.json");
  const gitClean = Buffer.from('{"compilerOptions":{"strict":true}}\n');
  await writeFile(path.join(projectDirectory, ".gitattributes"), "*.json text\n");
  await writeFile(projectFile, gitClean);
  assert.equal(git("add", ".gitattributes", "tsconfig.json").status, 0);
  assert.equal(git("commit", "-m", "fixture").status, 0);

  const {
    gitHeadObjectForProjectFileContents,
    restoreProjectFileSnapshot,
  } = await import(
    new URL("../servers/nextjs/scripts/run-workspace-e2e.mjs", import.meta.url)
  );
  const retainedOriginal = Buffer.from(
    '{"compilerOptions":{"strict":true}}\r\n',
  );
  const runnerGenerated = Buffer.from("runner generated bytes\n");
  const ownerToken = randomUUID();
  const capturedGitHeadObject = gitHeadObjectForProjectFileContents(
    projectFile,
    retainedOriginal,
  );
  assert.match(capturedGitHeadObject, /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u);
  await restoreProjectFileSnapshot(
    {
      file: projectFile,
      contents: retainedOriginal,
      gitHeadObject: capturedGitHeadObject,
      runnerGeneratedContents: runnerGenerated,
    },
    { lockDirectory, ownerToken },
  );
  assert.deepEqual(
    await readFile(projectFile),
    gitClean,
    "raw EOL differences are safe only when capture and recovery prove the same HEAD object",
  );

  const dirtyThirdState = Buffer.from("user-owned dirty third state\n");
  await writeFile(projectFile, dirtyThirdState);
  assert.equal(gitHeadObjectForProjectFileContents(projectFile, dirtyThirdState), undefined);
  await assert.rejects(
    restoreProjectFileSnapshot(
      {
        file: projectFile,
        contents: retainedOriginal,
        gitHeadObject: capturedGitHeadObject,
        runnerGeneratedContents: runnerGenerated,
      },
      { lockDirectory, ownerToken },
    ),
    (error) => error?.code === "EWORKSPACE_CONCURRENT_CHANGE",
  );
  assert.deepEqual(await readFile(projectFile), dirtyThirdState);

  await writeFile(projectFile, gitClean);
  const dirtyAtCapture = Buffer.from("user-owned bytes captured before run\n");
  assert.equal(
    gitHeadObjectForProjectFileContents(projectFile, dirtyAtCapture),
    undefined,
  );
  await assert.rejects(
    restoreProjectFileSnapshot(
      {
        file: projectFile,
        contents: dirtyAtCapture,
        runnerGeneratedContents: runnerGenerated,
      },
      { lockDirectory, ownerToken },
    ),
    (error) => error?.code === "EWORKSPACE_CONCURRENT_CHANGE",
  );
  assert.deepEqual(
    await readFile(projectFile),
    gitClean,
    "a dirty capture cannot authorize discarding its original snapshot",
  );
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
    "ChatSurface.cy.tsx",
    "WorkspaceChatPanel.cy.tsx",
    "SharePopover.cy.tsx",
    "DashboardPage.cy.tsx",
  ]) {
    assert.match(
      localRunner,
      new RegExp(requiredComponentSpec.replace(".", "\\."), "u"),
    );
  }
});

test("Dashboard component coverage is required on Bitbucket", () => {
  const pipeline = read("bitbucket-pipelines.yml");

  assert.match(
    pipeline,
    /name: Dashboard component gate[\s\S]*DashboardPage\.cy\.tsx/u,
  );
  assert.match(
    pipeline,
    /pull-requests:[\s\S]*\*dashboard-component-gate/u,
  );
  assert.match(pipeline, /branches:[\s\S]*\*dashboard-component-gate/u);
});

test("the structural layering gate is wired into the shared Next.js CI Node suite", () => {
  const rootPackage = readJson("package.json");

  assert.equal(
    rootPackage.scripts["check:structural-layering"],
    "node scripts/check-structural-layering.mjs",
  );
  assert.match(
    rootPackage.scripts["test:next-ci"],
    /npm run check:structural-layering/u,
  );
  assert.match(
    rootPackage.scripts["test:next-ci"],
    /scripts\/check-structural-layering\.test\.mjs/u,
  );
});

test("developer documentation matches the executable lint and test workflow", () => {
  const claude = read("CLAUDE.md");
  const nextPackage = readJson("servers/nextjs/package.json");
  const rootPackage = readJson("package.json");
  const nodeRunner = read("servers/nextjs/scripts/run-ci-node-tests.mjs");
  const pyproject = read("servers/fastapi/pyproject.toml");

  assert.deepEqual(nextPackage.scripts.lint, "npm run check:i18n && eslint .");
  assert.equal(nextPackage.scripts["test:ci-node"], "node scripts/run-ci-node-tests.mjs");
  assert.match(rootPackage.scripts["test:next-ci"], /test:ci-node/u);
  assert.match(pyproject, /select = \["E", "F", "B", "ASYNC"\]/u);
  assert.match(nodeRunner, /const ROOTS = \["app", "components", "i18n", "lib", "scripts", "store", "tests", "utils"\]/u);
  assert.match(claude, /Ruff is locked/u);
  assert.match(claude, /tests\/?`, and `utils\//u);
  assert.doesNotMatch(claude, /There is \*\*no ruff/u);
  assert.doesNotMatch(claude, /does \*\*not\*\* include `servers\/nextjs\/tests\/`/u);
});

// The authored-hybrid contracts used to be pinned by a GitHub-only Windows
// workflow that never ran for this fork. What still runs them is the shared
// Next.js Node suite on Bitbucket, which discovers every *.test.mjs under lib/
// except the dedicated end-to-end flows. The Hybrid export itself was removed
// with the authored strategy; the renderer isolation and the PPTX archive
// reader it shared with the renderer contract and OOXML vNext remain.
test("the Bitbucket Node suite discovers the authored-hybrid export contracts", () => {
  const nodeRunner = read("servers/nextjs/scripts/run-ci-node-tests.mjs");
  const exclusions = nodeRunner.slice(
    nodeRunner.indexOf("const EXCLUDED"),
    nodeRunner.indexOf("function walk"),
  );

  assert.match(nodeRunner, /const ROOTS = \[[^\]]*"lib"[^\]]*\]/u);
  assert.doesNotMatch(exclusions, /authored-hybrid/u);
  for (const contract of ["pptx-archive", "renderer-isolation", "security"]) {
    const relativePath = `servers/nextjs/lib/authored-hybrid/${contract}.test.mjs`;
    assert.ok(read(relativePath).length > 0, `${relativePath} must exist`);
    assert.doesNotMatch(
      relativePath,
      /\.e2e\.test\.mjs$/u,
      `${relativePath} must stay out of the end-to-end exclusion`,
    );
  }
});

// The standard Next.js build relies on this bounded symlink fallback on
// Windows (see the standalone-link-fallback test below).
test("the Windows standalone symlink fallback stays bounded", () => {
  const buildFallback = read(
    "servers/nextjs/scripts/next-standalone-symlink-fallback.cjs",
  );

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
  const routeSpec = read("servers/nextjs/cypress/e2e/canonicalConversationRoute.cy.ts");
  const liveSpec = read("servers/nextjs/cypress/component/CanonicalWorkspaceChatPanelLiveBackend.cy.tsx");

  assert.equal(nextPackage.devDependencies["axe-core"], "4.13.0");
  assert.match(runner, /cypress\/e2e\/canonicalConversationRoute\.cy\.ts/u);
  assert.match(runner, /cypress\/component\/CanonicalWorkspaceChatPanelLiveBackend\.cy\.tsx/u);
  assert.match(routeSpec, /node_modules\/axe-core\/axe\.min\.js/u);
  assert.match(routeSpec, /window\.Function\(source\)\(\)/u);
  assert.match(routeSpec, /axe-core did not attach to the application window/u);
  assert.match(routeSpec, /pageAxe\.run\(window\.document\)/u);
  assert.match(
    routeSpec,
    /operation-approval-alert[\s\S]{0,180}role", "alert"[\s\S]{0,180}aria-live", "assertive"/u,
  );
  assert.match(
    routeSpec,
    /\[role="status"\]\[aria-live="polite"\][\s\S]{0,80}have\.length", 1/u,
  );
  assert.match(routeSpec, /results\.violations[\s\S]{0,180}have\.length\(0\)/u);
  assert.match(liveSpec, /slide\.delete[\s\S]{0,240}operation_context/u);
});

test("the route gate proves an authoritative kill switch wins across real browsing contexts", () => {
  const spec = read("servers/nextjs/cypress/e2e/workspaceChatFirstRoute.cy.ts");
  const scenario = spec.slice(
    spec.indexOf(
      "revalidates command capability across two real browsing contexts without replacing the workspace",
    ),
    spec.indexOf(
      "keeps the bootstrap request as the first durable chat message",
    ),
  );

  assert.ok(scenario.length > 0, "capability rollback scenario is missing");
  assert.match(scenario, /capabilityResponse\s*=\s*disabledCapability/u);
  assert.match(spec, /const disabledCapability\s*=\s*\{[\s\S]*reason:\s*"chat_first_kill_switch_enabled"/u);
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
  assert.match(scenario, /workspace-timeline-scroll[\s\S]{0,80}be\.visible/u);
  assert.match(scenario, /secondary command revoke/u);
  assert.match(scenario, /capabilityResponse\s*=\s*enabledCapability/u);
  assert.match(
    scenario,
    /waitForWindowText\(secondaryWorkspaceWindow as Window, firstUserMessage\.content,/u,
  );
});

test("presentation creation and outline generation retain one stable deck identity", () => {
  const uploadState = read(
    "servers/nextjs/app/(presentation-generator)/upload/components/upload-page/useUploadPageState.ts",
  );
  const uploadHandlers = read(
    "servers/nextjs/app/(presentation-generator)/upload/components/upload-page/uploadGenerationHandlers.ts",
  );
  const outline = read(
    "servers/nextjs/app/(presentation-generator)/outline/hooks/usePresentationGeneration.ts",
  );

  assert.match(
    uploadState,
    /const \[pendingPresentationId,\s*setPendingPresentationId\]\s*=\s*useState<string>\(\s*\(\) => crypto\.randomUUID\(\),?\s*\)/u,
  );
  assert.match(
    uploadHandlers,
    /setPendingPresentationId\(crypto\.randomUUID\(\)\)/u,
    "a failed create attempt must rotate the deck id before an explicit retry",
  );
  assert.equal(
    uploadHandlers.match(/const presentationId = pendingPresentationId;/gu)?.length,
    2,
    "document and prompt-only creates must reuse the stable deck id",
  );
  assert.equal(
    uploadHandlers.match(/presentation_id: presentationId,/gu)?.length,
    2,
    "both create admissions must send that same id",
  );
  assert.equal(
    uploadHandlers.match(
      /idempotency_key: `presentation-create:\$\{presentationId\}`/gu,
    )?.length,
    2,
    "both create admissions must derive idempotency from the stable deck id",
  );
  // The outline hook once had four generation admissions; it now has a single
  // authored admission. Assert the invariant (every admission carries the
  // stable id) rather than a historical count.
  const outlineAdmissions =
    outline.match(/PresentationGenerationApi\.generate\w*\(\s*\{/gu)?.length ?? 0;
  assert.ok(
    outlineAdmissions >= 1,
    "the outline hook must still admit generation through the typed API",
  );
  assert.equal(
    outline.match(/presentation_id: presentationId,/gu)?.length,
    outlineAdmissions,
    "every outline generation admission must retain the stable deck id",
  );
});

// The startup shells render the product mark before anything else, so it is
// the LCP element and must be preloaded. The mark is /logo-with-bg.png; the
// retired /Logo.png wordmark is now only a test asset. A shell may also place
// the same file decoratively further down the page (AuthGate does, with an
// empty alt), and those copies must NOT be prioritized, so this requires one
// prioritized occurrence rather than demanding it of every occurrence.
test("every shell mark rendered during startup is prioritized for LCP", () => {
  const shellMark = 'src="/logo-with-bg.png"';
  for (const filePath of [
    "servers/nextjs/app/ConfigurationInitializer.tsx",
    "servers/nextjs/components/Auth/AuthGate.tsx",
    "servers/nextjs/components/OnBoarding/OnBoardingSlidebar.tsx",
  ]) {
    const source = read(filePath);
    let cursor = 0;
    let count = 0;
    let prioritized = 0;
    while ((cursor = source.indexOf(shellMark, cursor)) !== -1) {
      count += 1;
      if (/priority/u.test(source.slice(cursor, cursor + 320))) prioritized += 1;
      cursor += 1;
    }
    assert.ok(count > 0, `${filePath} must render the shell mark`);
    assert.ok(
      prioritized > 0,
      `${filePath} startup mark must be priority`,
    );
  }
});

// The Cypress gates name specs as globs. Cypress prints an unmatched glob and
// still exits zero, so a name that no longer resolves silently thins the gate
// instead of failing it. These helpers re-derive the answer from the pipeline
// and from cypress.config.ts rather than keeping a second copy of the list.
const NEXT_SERVER_DIRECTORY = new URL("../servers/nextjs/", import.meta.url);
const SPEC_WALK_SKIP = new Set([
  ".next",
  ".next-build",
  ".turbo",
  "coverage",
  "dist",
  "downloads",
  "node_modules",
  "out",
  "screenshots",
  "videos",
]);

function listCypressSpecFiles(relativeDirectory = "") {
  const found = [];
  const entries = readdirSync(
    new URL(relativeDirectory, NEXT_SERVER_DIRECTORY),
    { withFileTypes: true },
  );
  for (const entry of entries) {
    if (SPEC_WALK_SKIP.has(entry.name)) continue;
    const relative = `${relativeDirectory}${entry.name}`;
    if (entry.isDirectory()) {
      found.push(...listCypressSpecFiles(`${relative}/`));
    } else if (/\.cy\.[jt]sx?$/u.test(entry.name)) {
      found.push(relative);
    }
  }
  return found;
}

const escapeForRegExp = (text) =>
  text.replaceAll(/[.*+?^${}()|[\]\\]/gu, "\\$&");

function globToRegExp(glob) {
  let source = "";
  for (let index = 0; index < glob.length; index += 1) {
    const character = glob[index];
    if (character === "*" && glob[index + 1] === "*") {
      // A leading `**/` matches any number of directory segments, zero included.
      if (glob[index + 2] === "/") {
        source += "(?:[^/]+/)*";
        index += 2;
      } else {
        source += ".*";
        index += 1;
      }
    } else if (character === "*") {
      source += "[^/]*";
    } else if (character === "?") {
      source += "[^/]";
    } else if (character === "{") {
      const close = glob.indexOf("}", index);
      assert.notEqual(close, -1, `unterminated brace in glob ${glob}`);
      const alternatives = glob
        .slice(index + 1, close)
        .split(",")
        .map(escapeForRegExp);
      source += `(?:${alternatives.join("|")})`;
      index = close;
    } else {
      source += escapeForRegExp(character);
    }
  }
  return new RegExp(`^${source}$`, "u");
}

function cypressComponentSpecPatterns() {
  const config = read("servers/nextjs/cypress.config.ts");
  const block = config.match(/specPattern:\s*\[([^\]]*)\]/u)?.[1];
  assert.ok(
    block,
    "servers/nextjs/cypress.config.ts must declare a component specPattern array",
  );
  const patterns = [...block.matchAll(/"([^"]+)"/gu)].map((match) => match[1]);
  assert.ok(
    patterns.length > 0,
    "the component specPattern must list at least one spec root",
  );
  return patterns.map(globToRegExp);
}

function assertNamedSpecsResolve(specList, sourceLabel) {
  assert.ok(
    Array.isArray(specList) && specList.length > 0,
    `${sourceLabel} must name at least one Cypress spec`,
  );
  const reachable = cypressComponentSpecPatterns();
  const files = listCypressSpecFiles();
  for (const rawGlob of specList) {
    const glob = rawGlob.trim();
    const matcher = globToRegExp(glob);
    const matched = files.filter((file) => matcher.test(file));
    assert.notEqual(
      matched.length,
      0,
      `${sourceLabel} names Cypress spec "${glob}", but no such file exists under servers/nextjs`,
    );
    const runnable = matched.filter((file) =>
      reachable.some((pattern) => pattern.test(file)),
    );
    assert.notEqual(
      runnable.length,
      0,
      `${sourceLabel} names Cypress spec "${glob}", which resolves to ${matched.join(", ")}, but none of those fall inside the cypress.config.ts specPattern`,
    );
  }
}

test("every Cypress spec named by the local CI-parity runner exists and is reachable", () => {
  const localRunner = read("scripts/test-local.mjs");
  const block = localRunner.match(/const cypressSpecs = \[([^\]]*)\]/u)?.[1];
  assert.ok(
    block,
    "scripts/test-local.mjs must declare a cypressSpecs array",
  );
  const specList = [...block.matchAll(/"([^"]+)"/gu)].map((match) => match[1]);
  assertNamedSpecsResolve(specList, "scripts/test-local.mjs");
});

test("every Cypress spec named by the Bitbucket component gate exists and is reachable", () => {
  const pipeline = read("bitbucket-pipelines.yml");
  const specList = pipeline
    .match(/--component --spec "([^"]+)"/u)?.[1]
    ?.split(",");
  assertNamedSpecsResolve(specList, "bitbucket-pipelines.yml");
});
