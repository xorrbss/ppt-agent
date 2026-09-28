import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const scriptPath = path.join(repoRoot, "scripts", "check-structural-layering.mjs");

const {
  computeCurrent,
  diffAgainstAllowlist,
  loadAllowlist,
  ruleKeys,
} = await import(pathToFileURL(scriptPath).href);

async function writeFixture(root, relPath, contents) {
  const full = path.join(root, ...relPath.split("/"));
  await mkdir(path.dirname(full), { recursive: true });
  await writeFile(full, contents, "utf8");
}

test("each rule flags exactly the files it is documented to flag", async (t) => {
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "structural-layering-"));
  t.after(() => rm(fixtureRoot, { recursive: true, force: true }));

  // (a) utils/** importing services, including an indented lazy import.
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/utils/bad_utils.py",
    "def f():\n    from services import something\n    return something\n",
  );
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/utils/clean_utils.py",
    "import os\n\ndef f():\n    return os.getcwd()\n",
  );

  // (b) services/** importing api.
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/services/bad_service.py",
    "import api.v2.contracts as contracts\n",
  );
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/services/clean_service.py",
    "from services.other_thing import helper\n",
  );

  // (c) models/** importing services or api.
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/models/bad_model.py",
    "from services.presentation import thing\n",
  );
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/models/clean_model.py",
    "from pydantic import BaseModel\n",
  );

  // (d) globals()[...] = assignment, excluded from tests/vendor/etc.
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/services/bad_globals.py",
    "def bind_runtime(ns):\n    globals()['helper'] = ns['helper']\n",
  );
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/tests/unit/test_bad_globals.py",
    "def bind_runtime(ns):\n    globals()['helper'] = ns['helper']\n",
  );
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/vendor/slide_master/bad_globals.py",
    "def bind_runtime(ns):\n    globals()['helper'] = ns['helper']\n",
  );

  // (e) nextjs lib/store importing @/app/.
  await writeFixture(
    fixtureRoot,
    "servers/nextjs/lib/bad_lib.ts",
    'import { Slide } from "@/app/(presentation-generator)/types/slide";\n',
  );
  await writeFixture(
    fixtureRoot,
    "servers/nextjs/store/slices/bad_slice.ts",
    "import { Slide } from '@/app/(presentation-generator)/types/slide';\n",
  );
  await writeFixture(
    fixtureRoot,
    "servers/nextjs/lib/clean_lib.ts",
    'import { Slide } from "@/lib/types/slide";\n',
  );
  // A test fixture that merely contains the string as a literal (as the real
  // presentationGenUpload.source-locked.test.mjs does) must not be flagged.
  await writeFixture(
    fixtureRoot,
    "servers/nextjs/lib/fixture.test.mjs",
    "const s = 'import { X } from \"@/app/(presentation-generator)/x\";';\n",
  );

  const current = computeCurrent(fixtureRoot);

  assert.deepEqual(current.utils_import_services, [
    "servers/fastapi/utils/bad_utils.py",
  ]);
  assert.deepEqual(current.services_import_api, [
    "servers/fastapi/services/bad_service.py",
  ]);
  assert.deepEqual(current.models_import_services_or_api, [
    "servers/fastapi/models/bad_model.py",
  ]);
  assert.deepEqual(current.globals_injection, [
    "servers/fastapi/services/bad_globals.py",
  ]);
  assert.deepEqual(current.nextjs_lib_store_import_app, [
    "servers/nextjs/lib/bad_lib.ts",
    "servers/nextjs/store/slices/bad_slice.ts",
  ]);
});

test("diffAgainstAllowlist reports new violations and stale (fixed/removed) entries separately", () => {
  const current = {
    utils_import_services: ["servers/fastapi/utils/still_bad.py", "servers/fastapi/utils/newly_bad.py"],
    services_import_api: [],
    models_import_services_or_api: [],
    globals_injection: [],
    nextjs_lib_store_import_app: [],
  };
  const allowlist = {
    utils_import_services: ["servers/fastapi/utils/still_bad.py", "servers/fastapi/utils/fixed_already.py"],
    services_import_api: [],
    models_import_services_or_api: [],
    globals_injection: [],
    nextjs_lib_store_import_app: [],
  };

  const diff = diffAgainstAllowlist(current, allowlist);
  assert.deepEqual(diff.utils_import_services.newViolations, [
    "servers/fastapi/utils/newly_bad.py",
  ]);
  assert.deepEqual(diff.utils_import_services.stale, [
    "servers/fastapi/utils/fixed_already.py",
  ]);
  for (const key of ruleKeys()) {
    if (key === "utils_import_services") continue;
    assert.deepEqual(diff[key], { newViolations: [], stale: [] });
  }
});

test("CLI check mode exits non-zero on a new, unlisted violation", async (t) => {
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "structural-layering-cli-"));
  t.after(() => rm(fixtureRoot, { recursive: true, force: true }));
  await writeFixture(
    fixtureRoot,
    "servers/fastapi/utils/bad_utils.py",
    "from services import something\n",
  );
  // loadAllowlist() always reads the script's own fixed
  // scripts/structural-layering-allowlist.json path (not the fixture root),
  // so exercise the CLI end to end: --write seeds a passing baseline for the
  // one existing violation, then a second violation added afterwards must
  // fail the check without being written to the allowlist.
  const writeResult = spawnSync(
    process.execPath,
    [scriptPath, "--write", "--root", fixtureRoot],
    { encoding: "utf8" },
  );
  assert.equal(writeResult.status, 0, writeResult.stderr);

  const passResult = spawnSync(
    process.execPath,
    [scriptPath, "--root", fixtureRoot],
    { encoding: "utf8" },
  );
  assert.equal(passResult.status, 0, passResult.stderr);

  await writeFixture(
    fixtureRoot,
    "servers/fastapi/utils/another_bad.py",
    "from services import something_else\n",
  );
  const failResult = spawnSync(
    process.execPath,
    [scriptPath, "--root", fixtureRoot],
    { encoding: "utf8" },
  );
  assert.notEqual(failResult.status, 0);
  assert.match(failResult.stderr, /another_bad\.py/);
});

test("the committed allowlist matches the real repository tree exactly", () => {
  const allowlist = loadAllowlist();
  assert.ok(allowlist, "scripts/structural-layering-allowlist.json must exist");
  const current = computeCurrent(repoRoot);
  const diff = diffAgainstAllowlist(current, allowlist);
  for (const key of ruleKeys()) {
    assert.deepEqual(
      diff[key].newViolations,
      [],
      `unexpected new ${key} violation(s) not yet in the allowlist`,
    );
    assert.deepEqual(
      diff[key].stale,
      [],
      `${key} allowlist has stale entries that no longer violate or no longer exist`,
    );
  }
});

test("the raw script exits 0 against the real repository tree", () => {
  const result = spawnSync(process.execPath, [scriptPath], {
    encoding: "utf8",
    cwd: repoRoot,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /structural layering gate passed/);
});

test("the allowlist file itself is well-formed JSON with the five expected rule keys", () => {
  const raw = readFileSync(
    path.join(repoRoot, "scripts", "structural-layering-allowlist.json"),
    "utf8",
  );
  const parsed = JSON.parse(raw);
  assert.deepEqual(Object.keys(parsed).sort(), [...ruleKeys()].sort());
  for (const key of ruleKeys()) {
    assert.ok(Array.isArray(parsed[key]));
    const sorted = [...parsed[key]].sort();
    assert.deepEqual(parsed[key], sorted, `${key} entries must be sorted`);
  }
});
