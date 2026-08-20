import test from "node:test";
import assert from "node:assert/strict";
import {
  classifyWindowsIa32Probe,
  inspectBinary,
  validateArchiveEntries,
  PLATFORM_ARCHIVES,
} from "./verify-export-platform-archives.mjs";

function header(format, machine) {
  const buffer = Buffer.alloc(4096);
  if (format === "elf") {
    buffer.set([0x7f, 0x45, 0x4c, 0x46, 2, 1], 0);
    buffer.writeUInt16LE(machine, 18);
  } else if (format === "macho") {
    buffer.writeUInt32LE(0xfeedfacf, 0);
    buffer.writeUInt32LE(machine, 4);
  } else {
    buffer.set([0x4d, 0x5a], 0);
    buffer.writeUInt32LE(128, 0x3c);
    buffer.set([0x50, 0x45, 0, 0], 128);
    buffer.writeUInt16LE(machine, 132);
  }
  return buffer;
}

test("all six release targets have explicit ABI expectations", () => {
  assert.equal(PLATFORM_ARCHIVES.length, 6);
  for (const spec of PLATFORM_ARCHIVES) {
    assert.match(spec.name, /^export-(Linux|macOS|Windows)-/);
    assert.match(spec.binary, /^convert-/);
    assert.equal(typeof spec.machine, "number");
  }
});

test("ABI inspectors accept the expected ELF, Mach-O, and PE headers", () => {
  assert.deepEqual(inspectBinary(header("elf", 0x3e), "elf", 0x3e), { format: "elf", machine: "0x3e" });
  assert.deepEqual(inspectBinary(header("elf", 0xb7), "elf", 0xb7), { format: "elf", machine: "0xb7" });
  assert.deepEqual(inspectBinary(header("macho", 0x01000007), "macho", 0x01000007), { format: "macho", machine: "0x1000007" });
  assert.deepEqual(inspectBinary(header("macho", 0x0100000c), "macho", 0x0100000c), { format: "macho", machine: "0x100000c" });
  assert.deepEqual(inspectBinary(header("pe", 0x8664), "pe", 0x8664), { format: "pe", machine: "0x8664" });
  assert.deepEqual(inspectBinary(header("pe", 0x014c), "pe", 0x014c), { format: "pe", machine: "0x14c" });
});

test("ABI inspectors reject an architecture mismatch and unsafe ZIP paths", () => {
  assert.throws(() => inspectBinary(header("pe", 0x014c), "pe", 0x8664), /does not match expected/);
  assert.throws(() => validateArchiveEntries(["index.js", "../convert-win32-ia32.exe"], "convert-win32-ia32.exe"), /unsafe archive entry/);
  assert.throws(() => validateArchiveEntries(["index.js", "convert-win32-x64.exe", "extra"], "convert-win32-x64.exe"), /archive entries differ/);
});

test("ia32 runtime probe distinguishes WOW64 from a matching native tuple", () => {
  assert.equal(classifyWindowsIa32Probe("win32", "x64"), "windows-wow64-process-start");
  assert.equal(classifyWindowsIa32Probe("win32", "ia32"), "native-tuple-process-start");
  assert.equal(classifyWindowsIa32Probe("linux", "x64"), "unsupported-host");
});
