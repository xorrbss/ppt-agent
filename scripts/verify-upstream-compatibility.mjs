import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const compatibilityDir = path.join(repoRoot, "compatibility");
const migrationDir = path.join(repoRoot, "servers", "fastapi", "alembic", "versions");
const errors = [];
let checks = 0;

function check(condition, message) {
  checks += 1;
  if (!condition) errors.push(message);
}

async function readText(relativePath) {
  try {
    return await readFile(path.join(repoRoot, relativePath), "utf8");
  } catch (error) {
    errors.push(`cannot read ${relativePath}: ${error.message}`);
    return "";
  }
}

async function readJson(relativePath) {
  const text = await readText(relativePath);
  try {
    return JSON.parse(text);
  } catch (error) {
    errors.push(`invalid JSON in ${relativePath}: ${error.message}`);
    return {};
  }
}

function jsonPointer(document, pointer) {
  return pointer
    .split("/")
    .slice(1)
    .map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"))
    .reduce((value, key) => value?.[key], document);
}

function checkUnique(items, selector, label) {
  const values = items.map(selector);
  check(new Set(values).size === values.length, `${label} must be unique`);
}

function checkLinearChain(chain, commonRevision, tipRevision, label) {
  check(Array.isArray(chain) && chain.length > 0, `${label} must not be empty`);
  if (!Array.isArray(chain) || chain.length === 0) return;
  check(chain[0].downRevision === commonRevision, `${label} must start at ${commonRevision}`);
  for (let index = 1; index < chain.length; index += 1) {
    check(
      chain[index].downRevision === chain[index - 1].revision,
      `${label} is broken before ${chain[index].revision}`
    );
  }
  check(chain.at(-1).revision === tipRevision, `${label} tip must be ${tipRevision}`);
  checkUnique(chain, (entry) => entry.revision, `${label} revisions`);
}

function parseMigrationHeader(text) {
  const revision = text.match(/^revision(?:\s*:\s*[^=]+)?\s*=\s*["']([^"']+)["']/m)?.[1];
  const downRevision = text.match(/^down_revision(?:\s*:\s*[^=]+)?\s*=\s*["']([^"']+)["']/m)?.[1];
  return { revision, downRevision };
}

const manifest = await readJson("compatibility/upstream-compatibility.json");
const ledger = await readJson("compatibility/migration-translation-ledger.json");
const registry = await readJson("compatibility/protected-local-patches.json");
const upstreamTestContracts = await readJson(
  manifest.registries?.upstreamTestContracts ??
    "compatibility/upstream-test-contracts.json"
);
const remoteIntakePolicy = await readJson(
  manifest.registries?.remoteIntakePolicy ??
    "compatibility/upstream-intake-policy.json"
);

check(manifest.schemaVersion === 1, "manifest schemaVersion must be 1");
check(ledger.schemaVersion === 1, "migration ledger schemaVersion must be 1");
check(registry.schemaVersion === 1, "protected patch registry schemaVersion must be 1");
check(
  upstreamTestContracts.schemaVersion === 1,
  "upstream test contract registry schemaVersion must be 1"
);
check(
  remoteIntakePolicy.schemaVersion === 1,
  "remote intake policy schemaVersion must be 1"
);
check(
  /^[0-9a-f]{40}$/.test(manifest.baseline?.upstreamSha ?? ""),
  "baseline upstreamSha must be a full lowercase SHA"
);
check(
  ledger.baselineUpstreamSha === manifest.baseline?.upstreamSha,
  "manifest and migration ledger baseline SHAs differ"
);
check(
  upstreamTestContracts.baselineUpstreamSha === manifest.baseline?.upstreamSha,
  "manifest and upstream test contract baseline SHAs differ"
);
check(
  remoteIntakePolicy.baselineSha === manifest.baseline?.upstreamSha,
  "manifest and remote intake policy baseline SHAs differ"
);
check(
  remoteIntakePolicy.repository === manifest.baseline?.repository,
  "manifest and remote intake policy repositories differ"
);
check(
  ledger.remoteIntakeReview?.policy === manifest.registries?.remoteIntakePolicy,
  "migration ledger remote intake policy link differs"
);
check(
  registry.remoteIntakeReview?.policy === manifest.registries?.remoteIntakePolicy,
  "protected patch remote intake policy link differs"
);
checkUnique(remoteIntakePolicy.categories ?? [], (entry) => entry.id, "remote intake categories");
for (const category of remoteIntakePolicy.categories ?? []) {
  check(
    ["info", "review", "contract-risk"].includes(category.severity),
    `remote intake category ${category.id} has invalid severity`
  );
}

checkUnique(manifest.versions ?? [], (entry) => entry.id, "version ids");
for (const version of manifest.versions ?? []) {
  const source = await readJson(version.source);
  check(
    jsonPointer(source, version.jsonPointer) === version.value,
    `${version.id} expected ${version.value} at ${version.source}${version.jsonPointer}`
  );
  if (version.mirror) {
    const mirror = (await readText(version.mirror)).trim();
    check(mirror === version.value, `${version.id} mirror ${version.mirror} expected ${version.value}`);
  }
}

const rendererSource = await readText(manifest.templateV2Renderer?.source);
const actualDiscriminators = [
  ...rendererSource.matchAll(/element\.type === "([^"]+)"/g),
].map((match) => match[1]);
const expectedDiscriminators = manifest.templateV2Renderer?.discriminators ?? [];
check(expectedDiscriminators.length === 11, "renderer contract must contain exactly 11 discriminators");
checkUnique(expectedDiscriminators, (value) => value, "renderer discriminators");
check(
  JSON.stringify(actualDiscriminators) === JSON.stringify(expectedDiscriminators),
  `renderer discriminator drift: expected [${expectedDiscriminators}], got [${actualDiscriminators}]`
);

const payloadBoundarySource = await readText(
  manifest.templateV2PayloadBoundary?.source
);
const payloadBoundaryTest = await readText(
  manifest.templateV2PayloadBoundary?.test
);
const expectedWireShapes = [
  "array",
  "envelope",
  "nested-envelope",
];
check(
  JSON.stringify(manifest.templateV2PayloadBoundary?.wireShapes) ===
    JSON.stringify(expectedWireShapes),
  `Template V2 payload boundary must retain wire shapes [${expectedWireShapes}]`
);
for (const required of
  manifest.templateV2PayloadBoundary?.sourceRequiredContains ?? []) {
  check(
    payloadBoundarySource.includes(required),
    `Template V2 payload boundary source lost ${JSON.stringify(required)}`
  );
}
for (const required of
  manifest.templateV2PayloadBoundary?.testRequiredContains ?? []) {
  check(
    payloadBoundaryTest.includes(required),
    `Template V2 payload boundary test lost ${JSON.stringify(required)}`
  );
}

const routerSource = await readText(manifest.api?.routerSource);
check(
  routerSource.includes(`prefix="${manifest.api?.rootPrefix}"`),
  `API root router must retain ${manifest.api?.rootPrefix}`
);
for (const routerName of manifest.api?.routerIncludes ?? []) {
  check(
    routerSource.includes(`API_V1_PPT_ROUTER.include_router(${routerName})`),
    `API root router no longer includes ${routerName}`
  );
}
checkUnique(
  manifest.api?.keyEndpoints ?? [],
  (endpoint) => `${endpoint.method} ${endpoint.path}`,
  "key API endpoints"
);
for (const endpoint of manifest.api?.keyEndpoints ?? []) {
  check(endpoint.path.startsWith(manifest.api.rootPrefix), `${endpoint.path} is outside API root prefix`);
  const source = await readText(endpoint.source);
  for (const required of endpoint.requiredContains ?? []) {
    check(
      source.includes(required),
      `${endpoint.method} ${endpoint.path} lost source anchor ${JSON.stringify(required)}`
    );
  }
}

const reviewedUpstreamTests = [];
for (const source of upstreamTestContracts.sources ?? []) {
  check(
    typeof source.path === "string" && source.path.length > 0,
    "reviewed upstream test source requires a path"
  );
  for (const testContract of source.tests ?? []) {
    reviewedUpstreamTests.push(`${source.path}::${testContract.name}`);
    check(
      ["ported", "excluded"].includes(testContract.disposition),
      `${source.path}::${testContract.name} has an invalid disposition`
    );
    if (testContract.disposition === "excluded") {
      check(
        typeof testContract.reasonCode === "string" &&
          testContract.reasonCode.length > 0,
        `${source.path}::${testContract.name} requires an exclusion reasonCode`
      );
      check(
        typeof testContract.reason === "string" &&
          testContract.reason.length > 0,
        `${source.path}::${testContract.name} requires an exclusion reason`
      );
    }
  }
}
checkUnique(
  reviewedUpstreamTests,
  (reviewedTest) => reviewedTest,
  "reviewed upstream tests"
);
check(
  reviewedUpstreamTests.length === 14,
  `expected 14 reviewed upstream tests, got ${reviewedUpstreamTests.length}`
);
for (const excludedContract of
  upstreamTestContracts.excludedApiContracts ?? []) {
  check(
    typeof excludedContract.reasonCode === "string" &&
      excludedContract.reasonCode.length > 0,
    `${excludedContract.method} ${excludedContract.path} requires an exclusion reasonCode`
  );
  check(
    typeof excludedContract.reason === "string" &&
      excludedContract.reason.length > 0,
    `${excludedContract.method} ${excludedContract.path} requires an exclusion reason`
  );
  for (const evidenceFile of excludedContract.evidenceFiles ?? []) {
    check(
      (await readText(evidenceFile)).length > 0,
      `excluded API contract evidence missing: ${evidenceFile}`
    );
  }
}
checkUnique(
  upstreamTestContracts.portedContracts ?? [],
  (contract) => contract.id,
  "ported upstream contract ids"
);
for (const portedContract of upstreamTestContracts.portedContracts ?? []) {
  check(
    typeof portedContract.sourceConcept === "string" &&
      portedContract.sourceConcept.length > 0,
    `${portedContract.id} requires a source concept`
  );
  check(
    Array.isArray(portedContract.localTests) &&
      portedContract.localTests.length > 0,
    `${portedContract.id} requires at least one local regression test`
  );
  for (const localTest of portedContract.localTests ?? []) {
    const [testFile, testName] = localTest.split("::");
    const testSource = await readText(testFile);
    check(
      Boolean(testName) && testSource.includes(`def ${testName}(`),
      `${portedContract.id} local test anchor missing: ${localTest}`
    );
  }
  for (const evidenceFile of portedContract.evidenceFiles ?? []) {
    check(
      (await readText(evidenceFile)).length > 0,
      `${portedContract.id} evidence missing: ${evidenceFile}`
    );
  }
}

checkLinearChain(
  ledger.upstreamChain,
  ledger.commonRevision,
  ledger.upstreamTipRevision,
  "upstream migration chain"
);
checkLinearChain(
  ledger.localChain,
  ledger.commonRevision,
  ledger.localTipRevision,
  "local migration chain"
);

const upstreamRevisions = new Set((ledger.upstreamChain ?? []).map((entry) => entry.revision));
const localRevisions = new Set((ledger.localChain ?? []).map((entry) => entry.revision));
checkUnique(ledger.translations ?? [], (entry) => entry.upstreamRevision, "translation sources");
check(
  (ledger.translations ?? []).length === upstreamRevisions.size,
  "every upstream migration must have exactly one translation entry"
);
for (const translation of ledger.translations ?? []) {
  check(
    upstreamRevisions.has(translation.upstreamRevision),
    `translation references unknown upstream revision ${translation.upstreamRevision}`
  );
  check(
    typeof translation.note === "string" && translation.note.length > 0,
    `translation ${translation.upstreamRevision} requires a review note`
  );
  for (const revision of translation.localRevisions ?? []) {
    check(localRevisions.has(revision), `translation references unknown local revision ${revision}`);
  }
  for (const evidenceFile of translation.evidenceFiles ?? []) {
    check((await readText(evidenceFile)).length > 0, `translation evidence missing: ${evidenceFile}`);
  }
}

for (const migration of ledger.localChain ?? []) {
  const relativePath = path.relative(
    repoRoot,
    path.join(migrationDir, migration.file)
  ).replaceAll("\\", "/");
  const source = await readText(relativePath);
  const header = parseMigrationHeader(source);
  check(header.revision === migration.revision, `${migration.file} revision header drift`);
  check(header.downRevision === migration.downRevision, `${migration.file} down_revision header drift`);
}

let actualMigrationFiles = [];
try {
  actualMigrationFiles = (await readdir(migrationDir))
    .filter((file) => file.endsWith(".py") && file !== "__init__.py")
    .sort();
} catch (error) {
  errors.push(`cannot list local migrations: ${error.message}`);
}
const actualMigrations = [];
for (const file of actualMigrationFiles) {
  const relativePath = path.relative(
    repoRoot,
    path.join(migrationDir, file)
  ).replaceAll("\\", "/");
  const header = parseMigrationHeader(await readText(relativePath));
  check(Boolean(header.revision), `${file} is missing a revision header`);
  if (header.revision) {
    actualMigrations.push({
      revision: header.revision,
      downRevision: header.downRevision,
      file,
    });
  }
}
checkUnique(
  actualMigrations,
  (migration) => migration.revision,
  "actual local migration revisions"
);
const actualMigrationByRevision = new Map(
  actualMigrations.map((migration) => [migration.revision, migration])
);
const referencedDownRevisions = new Set(
  actualMigrations
    .map((migration) => migration.downRevision)
    .filter(Boolean)
);
const actualHeads = actualMigrations
  .filter((migration) => !referencedDownRevisions.has(migration.revision))
  .map((migration) => migration.revision)
  .sort();
check(
  actualHeads.length === 1 && actualHeads[0] === ledger.localTipRevision,
  `Alembic head drift: expected [${ledger.localTipRevision}], got [${actualHeads}]`
);

const actualLocalChain = [];
const visitedLocalRevisions = new Set();
let localCursor = ledger.localTipRevision;
while (localCursor && localCursor !== ledger.commonRevision) {
  if (visitedLocalRevisions.has(localCursor)) {
    errors.push(`cycle detected in local migration ancestry at ${localCursor}`);
    break;
  }
  visitedLocalRevisions.add(localCursor);
  const migration = actualMigrationByRevision.get(localCursor);
  if (!migration) {
    errors.push(`local migration ancestry is missing revision ${localCursor}`);
    break;
  }
  actualLocalChain.unshift(migration);
  localCursor = migration.downRevision;
}
check(
  localCursor === ledger.commonRevision,
  `local migration ancestry must reach common revision ${ledger.commonRevision}`
);
const ledgerLocalRevisions = (ledger.localChain ?? []).map(
  (migration) => migration.revision
);
const actualLocalRevisions = actualLocalChain.map(
  (migration) => migration.revision
);
check(
  JSON.stringify(actualLocalRevisions) ===
    JSON.stringify(ledgerLocalRevisions),
  `migration ledger omits or reorders local chain revisions: expected [${actualLocalRevisions}], recorded [${ledgerLocalRevisions}]`
);

for (const extension of ledger.localOnlyExtensions ?? []) {
  check(localRevisions.has(extension.revision), `unknown local-only extension ${extension.revision}`);
}

checkUnique(registry.patches ?? [], (entry) => entry.id, "protected patch ids");
const requiredCategories = new Set(["authored-hybrid", "windows-sync", "security"]);
for (const patch of registry.patches ?? []) {
  requiredCategories.delete(patch.category);
  check(
    typeof patch.rationale === "string" && patch.rationale.length > 0,
    `${patch.id} requires a rationale`
  );
  for (const file of patch.files ?? []) {
    const source = await readText(file.path);
    for (const required of file.requiredContains ?? []) {
      check(source.includes(required), `${patch.id}: ${file.path} lost ${JSON.stringify(required)}`);
    }
    for (const forbidden of file.forbiddenContains ?? []) {
      check(!source.includes(forbidden), `${patch.id}: ${file.path} contains forbidden ${JSON.stringify(forbidden)}`);
    }
  }
}
check(requiredCategories.size === 0, `missing protected patch categories: ${[...requiredCategories]}`);

if (errors.length > 0) {
  console.error(`Upstream compatibility verification failed (${errors.length}/${checks} checks):`);
  for (const error of errors) console.error(`- ${error}`);
  process.exitCode = 1;
} else {
  console.log(
    `Upstream compatibility verified: ${checks} checks; baseline ${manifest.baseline.upstreamSha}; ` +
      `${expectedDiscriminators.length} discriminators; ${manifest.api.keyEndpoints.length} key endpoints.`
  );
}
