const fs = require("fs");

const pkg = JSON.parse(fs.readFileSync("package.json"));
const releaseRepository = "xorrbss/ppt-agent";
let existing = {};
try {
  existing = JSON.parse(fs.readFileSync("version.json", "utf8"));
} catch (_) {}

const version = pkg.version;

const update = {
  version,
  message: process.env.UPDATE_MESSAGE || existing.message || "",
  downloads: {
    linux: `https://github.com/${releaseRepository}/releases/download/electron-v${version}/Presenton-${version}.deb`,
    mac: `https://github.com/${releaseRepository}/releases/download/electron-v${version}/Presenton-${version}.dmg`,
    windows: `https://github.com/${releaseRepository}/releases/download/electron-v${version}/Presenton-${version}.exe`
  }
};

fs.writeFileSync("version.json", `${JSON.stringify(update, null, 2)}\n`);

console.log("version.json generated");
