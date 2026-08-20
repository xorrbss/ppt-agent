import { createHash } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pipeline } from "node:stream/promises";
import { execFileSync, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const manifestPath = path.join(repoRoot, "compatibility", "presentation-export-v0.4.8-release-receipt.json");
const defaultReceiptPath = path.join(repoRoot, "compatibility", "presentation-export-v0.4.8-platform-receipt.json");

export const PLATFORM_ARCHIVES = Object.freeze([
  { name: "export-Linux-X64.zip", platform: "linux", architecture: "x64", format: "elf", machine: 0x3e, binary: "convert-linux-x64" },
  { name: "export-Linux-ARM64.zip", platform: "linux", architecture: "arm64", format: "elf", machine: 0xb7, binary: "convert-linux-arm64" },
  { name: "export-macOS-X64.zip", platform: "darwin", architecture: "x64", format: "macho", machine: 0x01000007, binary: "convert-darwin-x64" },
  { name: "export-macOS-ARM64.zip", platform: "darwin", architecture: "arm64", format: "macho", machine: 0x0100000c, binary: "convert-darwin-arm64" },
  { name: "export-Windows-X64.zip", platform: "win32", architecture: "x64", format: "pe", machine: 0x8664, binary: "convert-win32-x64.exe" },
  { name: "export-Windows-ia32.zip", platform: "win32", architecture: "ia32", format: "pe", machine: 0x014c, binary: "convert-win32-ia32.exe" },
]);

function fail(message) {
  throw new Error(message);
}

export function inspectBinary(buffer, format, expectedMachine, label = "binary") {
  if (!Buffer.isBuffer(buffer) || buffer.length < 64) fail(`${label} is shorter than the required header`);
  let machine;
  if (format === "elf") {
    if (buffer[0] !== 0x7f || buffer.toString("ascii", 1, 4) !== "ELF") fail(`${label} is not ELF`);
    if (buffer[4] !== 2 || buffer[5] !== 1) fail(`${label} must be ELF64 little-endian`);
    machine = buffer.readUInt16LE(18);
  } else if (format === "macho") {
    if (buffer.readUInt32LE(0) !== 0xfeedfacf) fail(`${label} is not little-endian Mach-O 64-bit`);
    machine = buffer.readUInt32LE(4);
  } else if (format === "pe") {
    if (buffer[0] !== 0x4d || buffer[1] !== 0x5a) fail(`${label} is not PE (missing MZ)`);
    const peOffset = buffer.readUInt32LE(0x3c);
    if (peOffset + 6 > buffer.length || buffer.toString("binary", peOffset, peOffset + 4) !== "PE\u0000\u0000") {
      fail(`${label} is not PE (missing signature)`);
    }
    machine = buffer.readUInt16LE(peOffset + 4);
  } else {
    fail(`unsupported binary format ${format}`);
  }
  if (machine !== expectedMachine) {
    fail(`${label} machine 0x${machine.toString(16)} does not match expected 0x${expectedMachine.toString(16)}`);
  }
  return { format, machine: `0x${machine.toString(16)}` };
}

export function validateArchiveEntries(entries, expectedBinary) {
  const normalized = entries.map((entry) => entry.replaceAll("\\", "/").replace(/^\.\//, ""));
  for (const entry of normalized) {
    if (!entry || entry.startsWith("/") || entry.split("/").includes("..")) fail(`unsafe archive entry: ${entry}`);
  }
  const expected = [expectedBinary, "index.js"].sort();
  const actual = [...new Set(normalized)].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    fail(`archive entries differ: expected ${expected.join(", ")}; received ${actual.join(", ")}`);
  }
  return actual;
}

export function classifyWindowsIa32Probe(hostPlatform = process.platform, hostArchitecture = process.arch) {
  if (hostPlatform !== "win32") return "unsupported-host";
  return hostArchitecture === "ia32" ? "native-tuple-process-start" : "windows-wow64-process-start";
}

function tarExecutable() {
  return process.platform === "win32" ? "tar.exe" : "tar";
}

function listArchive(archivePath) {
  const output = execFileSync(tarExecutable(), ["-tf", archivePath], {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
    windowsHide: true,
  });
  return output.split(/\r?\n/).filter(Boolean);
}

function readArchiveEntry(archivePath, entry) {
  return execFileSync(tarExecutable(), ["-xOf", archivePath, entry], {
    encoding: null,
    maxBuffer: 80 * 1024 * 1024,
    windowsHide: true,
  });
}

async function hashFile(filePath) {
  const hash = createHash("sha256");
  const stream = (await import("node:fs")).createReadStream(filePath);
  for await (const chunk of stream) hash.update(chunk);
  return hash.digest("hex");
}

async function downloadArchive(url, destination) {
  const response = await fetch(url);
  if (!response.ok || !response.body) fail(`download failed (${response.status}) for ${url}`);
  await pipeline(response.body, createWriteStream(destination));
}

async function runWindowsIa32Probe(archivePath) {
  if (process.platform !== "win32") {
    fail("--probe-windows-ia32 requires a Windows host");
  }
  const probeDir = await mkdtemp(path.join(os.tmpdir(), "presenton-export-ia32-probe-"));
  try {
    execFileSync(tarExecutable(), ["-xf", archivePath, "-C", probeDir], {
      windowsHide: true,
      stdio: "ignore",
    });
    const executable = path.join(probeDir, "convert-win32-ia32.exe");
    const executableSha256 = await hashFile(executable);
    const command = "convert-win32-ia32.exe --help";
    const child = spawnSync(executable, ["--help"], {
      cwd: probeDir,
      encoding: "utf8",
      timeout: 30_000,
      windowsHide: true,
      maxBuffer: 256 * 1024,
    });
    if (child.error && child.status === null) fail(`ia32 probe could not start: ${child.error.message}`);
    return {
      name: "Windows ia32 process-start input-validation",
      target: "export-Windows-ia32.zip",
      executedAt: new Date().toISOString(),
      host: { platform: process.platform, architecture: process.arch },
      classification: classifyWindowsIa32Probe(),
      command,
      exitCode: child.status,
      stdout: String(child.stdout ?? "").trim().slice(0, 4096),
      stderr: String(child.stderr ?? "").trim().slice(0, 4096),
      executableSha256,
      result: "process-started-input-validation-reached; conversion-not-proven",
    };
  } finally {
    await rm(probeDir, { recursive: true, force: true });
  }
}

async function loadExpectedReceipt() {
  const receipt = JSON.parse(await readFile(manifestPath, "utf8"));
  if (receipt.schemaVersion !== 1 || receipt.tag !== "v0.4.8") fail(`unexpected release receipt at ${manifestPath}`);
  const assets = new Map((receipt.assets ?? []).map((asset) => [asset.name, asset]));
  for (const spec of PLATFORM_ARCHIVES) {
    const asset = assets.get(spec.name);
    if (!asset || asset.matched !== true || asset.independentSha256 !== asset.githubDigest.replace(/^sha256:/, "")) {
      fail(`release receipt does not contain a matched digest for ${spec.name}`);
    }
  }
  return assets;
}

export async function verifyArchives({ archivesDir, writeReceipt = false, keep = false, probeWindowsIa32 = false } = {}) {
  const expected = await loadExpectedReceipt();
  const temporaryDir = archivesDir ? null : await mkdtemp(path.join(os.tmpdir(), "presenton-export-platform-"));
  const root = archivesDir ? path.resolve(archivesDir) : temporaryDir;
  await mkdir(root, { recursive: true });
  const observations = [];
  const runtimeProbes = [];
  try {
    for (const spec of PLATFORM_ARCHIVES) {
      const archivePath = path.join(root, spec.name);
      if (!archivesDir) {
        await downloadArchive(`https://github.com/presenton/presenton-export/releases/download/v0.4.8/${spec.name}`, archivePath);
      }
      const archiveStat = await stat(archivePath);
      const digest = await hashFile(archivePath);
      const asset = expected.get(spec.name);
      if (digest !== asset.independentSha256) fail(`${spec.name} SHA-256 mismatch: ${digest}`);
      if (asset.size !== archiveStat.size) fail(`${spec.name} size mismatch: ${archiveStat.size} (expected ${asset.size})`);
      const entries = validateArchiveEntries(listArchive(archivePath), spec.binary);
      const abi = inspectBinary(readArchiveEntry(archivePath, spec.binary).subarray(0, 4096), spec.format, spec.machine, spec.binary);
      observations.push({
        name: spec.name,
        platform: spec.platform,
        architecture: spec.architecture,
        size: archiveStat.size,
        sha256: digest,
        entries,
        binary: spec.binary,
        abi,
        nativeExecution: process.platform === spec.platform && process.arch === spec.architecture ? "eligible-not-attempted" : "not-attempted",
      });
      if (probeWindowsIa32 && spec.name === "export-Windows-ia32.zip") {
        runtimeProbes.push(await runWindowsIa32Probe(archivePath));
      }
    }
  } finally {
    if (temporaryDir && !keep) await rm(temporaryDir, { recursive: true, force: true });
  }
  const result = {
    schemaVersion: 1,
    repository: "presenton/presenton-export",
    tag: "v0.4.8",
    verifiedAt: new Date().toISOString(),
    verifier: "scripts/compatibility/verify-export-platform-archives.mjs",
    nativeHost: { platform: process.platform, architecture: process.arch },
    nativeExecutionPolicy: "ABI and archive structure are verified for every target; native conversion execution is reported only when the host tuple matches. A separate runtimeProbes entry may record Windows WOW64 process start and input validation without treating it as native execution or conversion success.",
    archives: observations,
    runtimeProbes,
  };
  if (writeReceipt) {
    await mkdir(path.dirname(defaultReceiptPath), { recursive: true });
    const receiptPath = typeof writeReceipt === "string" ? path.resolve(writeReceipt) : defaultReceiptPath;
    await writeFile(receiptPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    result.receiptPath = path.relative(repoRoot, receiptPath).replaceAll("\\", "/");
  }
  return result;
}

function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--archives-dir") options.archivesDir = argv[++i];
    else if (argv[i] === "--write-receipt") {
      const next = argv[i + 1];
      options.writeReceipt = next && !next.startsWith("--") ? argv[++i] : true;
    }
    else if (argv[i] === "--keep") options.keep = true;
    else if (argv[i] === "--probe-windows-ia32") options.probeWindowsIa32 = true;
    else if (argv[i] === "--help") options.help = true;
    else fail(`unknown argument ${argv[i]}`);
  }
  return options;
}

if (process.argv[1] && path.resolve(fileURLToPath(import.meta.url)) === path.resolve(process.argv[1])) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) {
      console.log("Usage: node scripts/compatibility/verify-export-platform-archives.mjs [--archives-dir DIR] [--write-receipt [PATH]] [--keep]");
    } else {
      const result = await verifyArchives(options);
      console.log(`PASS: verified ${result.archives.length} v0.4.8 platform archives (${result.nativeHost.platform}/${result.nativeHost.architecture})`);
      for (const archive of result.archives) console.log(`- ${archive.name}: ${archive.sha256} ${archive.abi.format}/${archive.abi.machine}`);
    }
  } catch (error) {
    console.error(`FAIL: ${error.message}`);
    process.exitCode = 1;
  }
}
