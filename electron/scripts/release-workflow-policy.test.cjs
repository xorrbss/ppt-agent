const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const workflowsDirectory = path.resolve(
  __dirname,
  "..",
  "..",
  ".github",
  "workflows"
);
const uploadArtifactRef =
  "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a";
const downloadArtifactRef =
  "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c";

function readWorkflow(name) {
  return fs.readFileSync(path.join(workflowsDirectory, name), "utf8");
}

test("artifact actions use the reviewed Node 24 releases", () => {
  const workflowSources = fs
    .readdirSync(workflowsDirectory)
    .filter((name) => /\.ya?ml$/u.test(name))
    .map((name) => [name, readWorkflow(name)]);
  const artifactUses = workflowSources.flatMap(([name, source]) =>
    [...source.matchAll(/uses:\s+(actions\/(?:upload|download)-artifact@[^\s#]+)/gu)]
      .map((match) => ({ name, value: match[1] }))
  );

  assert.ok(artifactUses.length > 0, "at least one artifact action is expected");
  for (const artifactUse of artifactUses) {
    const expected = artifactUse.value.startsWith("actions/upload-artifact@")
      ? uploadArtifactRef
      : downloadArtifactRef;
    assert.equal(
      artifactUse.value,
      expected,
      `${artifactUse.name} must pin the reviewed Node 24 artifact action`
    );
  }
});

test("R2 synchronization fails closed before it uploads release assets", () => {
  const source = readWorkflow("sync-releaes-to-r2.yml");
  const credentialGate = source.indexOf(
    "operational-release-preflight.mjs release --require-r2"
  );
  const releaseValidation = source.indexOf(
    "Validate published release and resolve version"
  );
  const download = source.indexOf("gh release download");
  const checksumGate = source.indexOf("sha256sum --check");
  const probe = source.indexOf("Probe R2 write, read, and delete access");
  const upload = source.indexOf("rclone copy assets");
  const remoteCheck = source.indexOf("rclone check assets");

  for (const [name, index] of Object.entries({
    credentialGate,
    releaseValidation,
    download,
    checksumGate,
    probe,
    upload,
    remoteCheck,
  })) {
    assert.notEqual(index, -1, `R2 workflow is missing ${name}`);
  }
  assert.ok(credentialGate < download, "credentials must gate asset download");
  assert.ok(releaseValidation < download, "release metadata must gate download");
  assert.ok(download < checksumGate, "downloaded assets must be checksum-gated");
  assert.ok(checksumGate < probe, "checksums must pass before external mutation");
  assert.ok(probe < upload, "reversible access probe must pass before upload");
  assert.ok(upload < remoteCheck, "uploaded objects must be checked remotely");
  assert.doesNotMatch(source, /curl\s+https:\/\/rclone\.org\/install\.sh\s*\|\s*sudo/);
  assert.doesNotMatch(source, /rclone\.conf/);
  assert.match(source, /RCLONE_CONFIG_R2_SECRET_ACCESS_KEY/);
  assert.match(source, /rclone deletefile "\$probe_remote"/);
});
