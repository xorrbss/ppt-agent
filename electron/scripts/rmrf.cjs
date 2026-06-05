#!/usr/bin/env node
// Cross-platform recursive delete — replaces Unix `rm -rf` in npm scripts so
// they run on Windows (cmd.exe has no `rm`). Zero dependencies. Paths are
// resolved relative to the current working directory (the electron package).
const fs = require("fs");
const path = require("path");

const targets = process.argv.slice(2);
if (targets.length === 0) {
  console.error("rmrf: no paths given");
  process.exit(1);
}

for (const target of targets) {
  fs.rmSync(path.resolve(target), { recursive: true, force: true });
}
