const fs = require("node:fs");
const path = require("node:path");

function readExpectedAppxVersion(options = {}) {
  const electronRoot = path.resolve(options.electronRoot || path.join(__dirname, ".."));
  const electronPackage = JSON.parse(
    fs.readFileSync(path.join(electronRoot, "package.json"), "utf8")
  );
  const parts = String(electronPackage.version || "").split(".");
  if (
    parts.length !== 3 ||
    parts.some((part) => !/^\d+$/.test(part) || Number(part) > 65535)
  ) {
    throw new Error(
      `Electron version must be three numeric AppX-safe components: ${electronPackage.version || "(missing)"}`
    );
  }
  return `${parts.join(".")}.0`;
}

function readExpectedExportVersion(options = {}) {
  const repoRoot = path.resolve(options.repoRoot || path.join(__dirname, "..", ".."));
  const electronRoot = path.resolve(options.electronRoot || path.join(__dirname, ".."));
  const repoPackage = JSON.parse(fs.readFileSync(path.join(repoRoot, "package.json"), "utf8"));
  const electronPackage = JSON.parse(
    fs.readFileSync(path.join(electronRoot, "package.json"), "utf8")
  );
  const repoVersion = repoPackage.presentationExportVersion;
  const electronVersion = electronPackage.exportVersion;
  if (!repoVersion || !electronVersion || repoVersion !== electronVersion) {
    throw new Error(
      `presentation-export version mismatch: root=${repoVersion || "(missing)"}, ` +
        `electron=${electronVersion || "(missing)"}`
    );
  }
  return repoVersion;
}

module.exports = { readExpectedAppxVersion, readExpectedExportVersion };
