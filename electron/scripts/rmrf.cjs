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

const workingDirectory = path.resolve(process.cwd());

function isSafeBuildTarget(target) {
  const resolved = path.resolve(target);
  const relative = path.relative(workingDirectory, resolved);
  const pathSegments = relative.split(path.sep).filter(Boolean);
  const workingDirectorySegments = workingDirectory.split(path.sep).filter(Boolean);

  if (
    workingDirectorySegments.some((segment) =>
      segment.toLowerCase().startsWith(".tmp-")
    ) ||
    relative === "" ||
    relative === ".." ||
    relative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relative)
  ) {
    return false;
  }

  // Temporary worktrees are user-owned investigation state. A build cleanup
  // must never reach them, even when the caller happens to run inside one.
  return !pathSegments.some((segment) => segment.toLowerCase().startsWith(".tmp-"));
}

for (const target of targets) {
  if (!isSafeBuildTarget(target)) {
    console.error(`rmrf: refusing unsafe target: ${target}`);
    process.exitCode = 1;
    continue;
  }

  const resolved = path.resolve(target);
  // Do not traverse an unexpected link when reclaiming build artifacts. The
  // normal build outputs are directories, so this fails closed on corruption.
  if (fs.existsSync(resolved) && fs.lstatSync(resolved).isSymbolicLink()) {
    console.error(`rmrf: refusing symbolic-link target: ${target}`);
    process.exitCode = 1;
    continue;
  }

  fs.rmSync(resolved, { recursive: true, force: true });
}
