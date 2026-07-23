const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const {
  getUpdateDownloadUrl,
  isNewerVersion,
  isValidReleaseVersion,
} = require("../app_dist/utils/update-channel.js");

test("validates and compares release versions without stripping prerelease semantics", () => {
  assert.equal(isValidReleaseVersion("2026.7.2401"), true);
  assert.equal(isValidReleaseVersion("0.9.2-pptagent.1"), true);
  assert.equal(isValidReleaseVersion("not-a-version"), false);
  assert.equal(isNewerVersion("2026.7.2401", "2026.7.2402"), true);
  assert.equal(isNewerVersion("1.0.0-beta.1", "1.0.0"), true);
  assert.equal(isNewerVersion("1.0.0", "1.0.0-beta.2"), false);
});

test("selects only the fork-owned release asset for the active platform", () => {
  const version = "2026.7.2401";
  const downloads = {
    windows: `https://github.com/xorrbss/ppt-agent/releases/download/electron-v${version}/Presenton-${version}.exe`,
    mac: `https://github.com/xorrbss/ppt-agent/releases/download/electron-v${version}/Presenton-${version}.dmg`,
    linux: `https://github.com/xorrbss/ppt-agent/releases/download/electron-v${version}/Presenton-${version}.deb`,
  };
  assert.equal(getUpdateDownloadUrl(version, downloads, "win32"), downloads.windows);
  assert.equal(getUpdateDownloadUrl(version, downloads, "darwin"), downloads.mac);
  assert.equal(getUpdateDownloadUrl(version, downloads, "linux"), downloads.linux);
});

test("rejects an upstream, insecure, or wrong-tag download URL", () => {
  const fallback = "https://github.com/xorrbss/ppt-agent/releases/latest";
  assert.equal(
    getUpdateDownloadUrl(
      "2026.7.2401",
      { windows: "https://github.com/presenton/presenton/releases/download/electron-v2026.7.2401/Presenton.exe" },
      "win32"
    ),
    fallback
  );
  assert.equal(
    getUpdateDownloadUrl(
      "2026.7.2401",
      { windows: "http://github.com/xorrbss/ppt-agent/releases/download/electron-v2026.7.2401/Presenton.exe" },
      "win32"
    ),
    fallback
  );
  assert.equal(
    getUpdateDownloadUrl(
      "2026.7.2401",
      { windows: "https://github.com/xorrbss/ppt-agent/releases/download/electron-v2026.7.2402/Presenton.exe" },
      "win32"
    ),
    fallback
  );
});

test("generator emits only fork-owned release URLs", () => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "ppt-agent-update-"));
  try {
    fs.copyFileSync(path.join(__dirname, "..", "generate_update.js"), path.join(tempDir, "generate_update.js"));
    fs.writeFileSync(
      path.join(tempDir, "package.json"),
      JSON.stringify({ version: "2026.7.2401" })
    );
    fs.writeFileSync(path.join(tempDir, "version.json"), JSON.stringify({ message: "RC" }));

    const result = spawnSync(process.execPath, ["generate_update.js"], {
      cwd: tempDir,
      encoding: "utf8",
    });
    assert.equal(result.status, 0, result.stderr);
    const generated = JSON.parse(fs.readFileSync(path.join(tempDir, "version.json"), "utf8"));
    const serialized = JSON.stringify(generated);
    assert.equal(generated.version, "2026.7.2401");
    assert.equal(generated.message, "RC");
    assert.match(serialized, /xorrbss\/ppt-agent/);
    assert.doesNotMatch(serialized, /presenton\/presenton/);
  } finally {
    fs.rmSync(tempDir, { recursive: true, force: true });
  }
});
