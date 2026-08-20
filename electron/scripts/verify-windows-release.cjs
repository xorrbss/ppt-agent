const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { requireReadinessOverlay } = require("./export-readiness-verifier.cjs");
const {
  readExpectedAppxVersion,
  readExpectedExportVersion,
} = require("./release-version-contract.cjs");

const EXPECTED_EXPORT_VERSION = "v0.4.8";
const EXPECTED_IDENTITY = "PresentonAI.Presenton";
const EXPECTED_PUBLISHER = "CN=8A2C57B5-F1C6-473A-93EE-2E9B72134341";
const APP_RESOURCE_PREFIX = "app/resources/app/resources/";

const EXPECTED_APPX_VERSION = readExpectedAppxVersion();

function requireFile(filePath, label, minimumBytes = 1) {
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch {
    throw new Error(`${label} is missing: ${filePath}`);
  }
  if (!stat.isFile() || stat.size < minimumBytes) {
    throw new Error(`${label} is invalid: ${filePath}`);
  }
}

function requireDirectoryWithFiles(directory, label) {
  let stat;
  try {
    stat = fs.statSync(directory);
  } catch {
    throw new Error(`${label} is missing: ${directory}`);
  }
  if (!stat.isDirectory()) throw new Error(`${label} is not a directory: ${directory}`);
  const pending = [directory];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      if (entry.isFile()) return;
      if (entry.isDirectory()) {
        pending.push(path.join(current, entry.name));
      }
    }
  }
  throw new Error(`${label} contains no files: ${directory}`);
}

function inspectPeBuffer(buffer, label) {
  if (buffer.length < 64 || buffer[0] !== 0x4d || buffer[1] !== 0x5a) {
    throw new Error(`${label} is not a PE file (missing MZ header)`);
  }
  const peOffset = buffer.readUInt32LE(0x3c);
  if (
    peOffset + 6 > buffer.length ||
    buffer.toString("binary", peOffset, peOffset + 4) !== "PE\u0000\u0000"
  ) {
    throw new Error(`${label} is not a PE file (missing PE signature)`);
  }
  const machine = buffer.readUInt16LE(peOffset + 4);
  if (![0x014c, 0x8664, 0xaa64].includes(machine)) {
    throw new Error(`${label} has unsupported PE machine 0x${machine.toString(16)}`);
  }
  return { machine };
}

function inspectPeFile(filePath, label) {
  requireFile(filePath, label, 64);
  const descriptor = fs.openSync(filePath, "r");
  try {
    const dos = Buffer.alloc(64);
    fs.readSync(descriptor, dos, 0, dos.length, 0);
    const peOffset = dos.readUInt32LE(0x3c);
    const header = Buffer.alloc(peOffset + 24);
    fs.readSync(descriptor, header, 0, header.length, 0);
    return inspectPeBuffer(header, label);
  } finally {
    fs.closeSync(descriptor);
  }
}

function defaultSignatureCheck(filePath) {
  const script = [
    "$s=Get-AuthenticodeSignature -LiteralPath $env:PRESENTON_SIGN_TARGET",
    [
      "[pscustomobject]@{Status=[string]$s.Status;StatusMessage=$s.StatusMessage;",
      "Subject=$(if($s.SignerCertificate){$s.SignerCertificate.Subject}else{$null});",
      "Thumbprint=$(if($s.SignerCertificate){$s.SignerCertificate.Thumbprint}else{$null});",
      "TimestampSubject=$(if($s.TimeStamperCertificate){$s.TimeStamperCertificate.Subject}else{$null})}",
      "| ConvertTo-Json -Compress",
    ].join(""),
  ].join(";");
  const candidates = ["pwsh.exe", "powershell.exe"];
  let lastError;
  for (const executable of candidates) {
    try {
      const output = execFileSync(
        executable,
        ["-NoProfile", "-NonInteractive", "-Command", script],
        {
          encoding: "utf8",
          env: { ...process.env, PRESENTON_SIGN_TARGET: path.resolve(filePath) },
          windowsHide: true,
        }
      );
      return JSON.parse(output);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

function requireValidSignature(filePath, label, signatureCheck) {
  const signature = signatureCheck(filePath);
  if (signature.Status !== "Valid") {
    throw new Error(
      `${label} Authenticode status is ${signature.Status || "Unknown"}: ${filePath}`
    );
  }
  if (!signature.Subject || !signature.Thumbprint) {
    throw new Error(`${label} has no signer identity: ${filePath}`);
  }
  return signature;
}

function listArchive(archivePath, tarCommand = "tar.exe") {
  const output = execFileSync(tarCommand, ["-tf", archivePath], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    windowsHide: true,
  });
  return new Set(
    output
      .split(/\r?\n/)
      .map((entry) => entry.replaceAll("\\", "/").replace(/^\.?\//, ""))
      .filter(Boolean)
  );
}

function readArchiveEntry(archivePath, entry, tarCommand = "tar.exe") {
  return execFileSync(tarCommand, ["-xOf", archivePath, entry], {
    encoding: null,
    maxBuffer: 16 * 1024 * 1024,
    windowsHide: true,
  });
}

function readArchiveEntryPrefix(archivePath, entry, maximumBytes = 4096) {
  const script =
    "Add-Type -AssemblyName System.IO.Compression.FileSystem;" +
    "$z=[System.IO.Compression.ZipFile]::OpenRead($env:PRESENTON_ARCHIVE);" +
    "try{" +
    "$e=$z.GetEntry($env:PRESENTON_ARCHIVE_ENTRY);" +
    "if(!$e){throw 'entry missing'};" +
    "$s=$e.Open();" +
    "try{" +
    "$b=New-Object byte[] $env:PRESENTON_PREFIX_BYTES;" +
    "$n=$s.Read($b,0,$b.Length);" +
    "[Convert]::ToBase64String($b,0,$n)" +
    "}finally{$s.Dispose()}" +
    "}finally{$z.Dispose()}";
  const output = execFileSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", script],
    {
      encoding: "utf8",
      env: {
        ...process.env,
        PRESENTON_ARCHIVE: path.resolve(archivePath),
        PRESENTON_ARCHIVE_ENTRY: entry,
        PRESENTON_PREFIX_BYTES: String(maximumBytes),
      },
      windowsHide: true,
    }
  );
  return Buffer.from(output.trim(), "base64");
}

function xmlTagAttributes(xml, tagName) {
  const match = xml.match(new RegExp(`<${tagName}\\b([^>]*)>`, "i"));
  if (!match) throw new Error(`AppX manifest is missing <${tagName}>`);
  const attributes = {};
  for (const item of match[1].matchAll(/([:\w-]+)\s*=\s*(['"])(.*?)\2/g)) {
    attributes[item[1]] = item[3];
  }
  return attributes;
}

function findEntry(entries, candidates, label) {
  const found = candidates.find((candidate) => entries.has(candidate));
  if (!found) {
    throw new Error(`${label} is missing from AppX (${candidates.join(" or ")})`);
  }
  return found;
}

function findEntryBy(entries, predicate, label) {
  const found = [...entries].find(predicate);
  if (!found) throw new Error(`${label} is missing from AppX`);
  return found;
}

function validateAppx(appxPath, options = {}) {
  const expectedExportVersion =
    options.expectedExportVersion || EXPECTED_EXPORT_VERSION;
  const expectedAppxVersion =
    options.expectedAppxVersion || EXPECTED_APPX_VERSION;
  requireFile(appxPath, "AppX package", 4);
  const zipMagic = Buffer.alloc(4);
  const descriptor = fs.openSync(appxPath, "r");
  try {
    fs.readSync(descriptor, zipMagic, 0, zipMagic.length, 0);
  } finally {
    fs.closeSync(descriptor);
  }
  if (zipMagic[0] !== 0x50 || zipMagic[1] !== 0x4b) {
    throw new Error(`AppX is not a ZIP container: ${appxPath}`);
  }

  const tarCommand = options.tarCommand || "tar.exe";
  const entries = (options.archiveReader || {
    list: (file) => listArchive(file, tarCommand),
    read: (file, entry) => readArchiveEntry(file, entry, tarCommand),
    readPrefix: (file, entry) => readArchiveEntryPrefix(file, entry),
  });
  const readPrefix = (entry) =>
    entries.readPrefix
      ? entries.readPrefix(appxPath, entry)
      : entries.read(appxPath, entry).subarray(0, 4096);
  const names = entries.list(appxPath);
  const structuralEntries = [
    "AppxManifest.xml",
    "AppxBlockMap.xml",
    "[Content_Types].xml",
  ];
  if (options.requireSignature) structuralEntries.push("AppxSignature.p7x");
  for (const structural of structuralEntries) {
    if (!names.has(structural)) {
      throw new Error(`AppX ZIP structure is missing ${structural}`);
    }
  }

  const manifest = entries.read(appxPath, "AppxManifest.xml").toString("utf8");
  const identity = xmlTagAttributes(manifest, "Identity");
  const application = xmlTagAttributes(manifest, "Application");
  if (identity.Name !== EXPECTED_IDENTITY) {
    throw new Error(`Unexpected AppX Identity Name: ${identity.Name}`);
  }
  if (identity.Publisher !== EXPECTED_PUBLISHER) {
    throw new Error(`Unexpected AppX Publisher: ${identity.Publisher}`);
  }
  if (identity.Version !== expectedAppxVersion) {
    throw new Error(
      `Unexpected AppX Identity Version: ${identity.Version}; expected ${expectedAppxVersion}`
    );
  }
  if (!["x64", "arm64"].includes(identity.ProcessorArchitecture)) {
    throw new Error(
      `Unsupported AppX ProcessorArchitecture: ${identity.ProcessorArchitecture}`
    );
  }

  const executable = application.Executable.replaceAll("\\", "/");
  if (!names.has(executable)) {
    throw new Error(`AppX manifest executable is missing from ZIP: ${executable}`);
  }
  inspectPeBuffer(readPrefix(executable), "AppX application executable");

  const resource = (relative) => `${APP_RESOURCE_PREFIX}${relative}`;
  const marker = findEntry(
    names,
    [resource("export/.installed-version")],
    "export marker"
  );
  const markerVersion = entries.read(appxPath, marker).toString("utf8").trim();
  if (markerVersion !== expectedExportVersion) {
    throw new Error(
      `AppX export marker must be ${expectedExportVersion}, received ${markerVersion}`
    );
  }
  const exportIndex = findEntry(
    names,
    [resource("export/index.js")],
    "export index.js"
  );
  requireReadinessOverlay(entries.read(appxPath, exportIndex), "AppX export index.js");
  const converter = findEntryBy(
    names,
    (name) => /^app\/resources\/app\/resources\/export\/py\/convert-win32-(x64|arm64)\.exe$/i.test(name),
    "export converter"
  );
  const fastapi = findEntry(
    names,
    [resource("fastapi/fastapi.exe")],
    "FastAPI executable"
  );
  findEntry(
    names,
    [resource("contracts/authored_vnext/ooxml/v1/generated/ooxml_package.py")],
    "authored OOXML contract"
  );
  const templateRegistry = findEntry(
    names,
    [resource("templates/package-registry.v1.json")],
    "template package registry"
  );
  findEntry(
    names,
    [resource("contracts/template_package/engines/template-v2-json.v1.json")],
    "template package engine"
  );
  findEntry(
    names,
    [resource("servers/nextjs/lib/template-v2-json-to-html.ts")],
    "template package renderer evidence"
  );
  const registry = JSON.parse(entries.read(appxPath, templateRegistry).toString("utf8"));
  if (registry.legacyCompatibility?.enabled !== false) {
    throw new Error("AppX template registry must keep legacyCompatibility.enabled=false");
  }
  inspectPeBuffer(readPrefix(converter), "AppX export converter");
  inspectPeBuffer(readPrefix(fastapi), "AppX FastAPI executable");

  const nextServer = findEntry(
    names,
    [
      resource("nextjs/server.js"),
      resource("nextjs/servers/nextjs/server.js"),
    ],
    "Next.js server.js"
  );
  const nextRoot = nextServer.slice(0, -"server.js".length);
  findEntryBy(
    names,
    (name) => name.startsWith(`${nextRoot}.next-build/static/`) && !name.endsWith("/"),
    "Next.js static assets"
  );
  findEntryBy(
    names,
    (name) => name.startsWith(`${nextRoot}public/`) && !name.endsWith("/"),
    "Next.js public assets"
  );

  return {
    architecture: identity.ProcessorArchitecture,
    converter,
    executable,
    fastapi,
    nextServer,
    version: identity.Version,
  };
}

function discoverArtifacts(distRoot) {
  const files = fs.readdirSync(distRoot, { withFileTypes: true });
  const installers = files
    .filter((entry) => entry.isFile() && /\.exe$/i.test(entry.name))
    .map((entry) => path.join(distRoot, entry.name));
  const appx = files
    .filter((entry) => entry.isFile() && /\.appx$/i.test(entry.name))
    .map((entry) => path.join(distRoot, entry.name));
  if (installers.length !== 1) {
    throw new Error(`Expected exactly one NSIS .exe, found ${installers.length}`);
  }
  if (appx.length !== 1) {
    throw new Error(`Expected exactly one .appx, found ${appx.length}`);
  }
  return { installer: installers[0], appx: appx[0] };
}

function validateUnpacked(
  distRoot,
  signatureCheck,
  verifiedSignature,
  requireSignature,
  expectedExportVersion
) {
  const unpacked = path.join(distRoot, "win-unpacked");
  if (!fs.existsSync(unpacked)) return null;
  const executable = path.join(unpacked, "Presenton.exe");
  inspectPeFile(executable, "unpacked Presenton.exe");
  const signature = requireSignature
    ? verifiedSignature ||
      requireValidSignature(
        executable,
        "unpacked Presenton.exe",
        signatureCheck
      )
    : null;
  const resources = path.join(unpacked, "resources", "app", "resources");
  const marker = path.join(resources, "export", ".installed-version");
  requireFile(marker, "unpacked export marker");
  if (fs.readFileSync(marker, "utf8").trim() !== expectedExportVersion) {
    throw new Error(`unpacked export marker must be ${expectedExportVersion}`);
  }
  const exportIndex = path.join(resources, "export", "index.js");
  requireFile(exportIndex, "unpacked export index.js");
  requireReadinessOverlay(
    fs.readFileSync(exportIndex),
    "unpacked export index.js"
  );
  const converterRoot = path.join(resources, "export", "py");
  const converter = fs
    .readdirSync(converterRoot)
    .find((name) => /^convert-win32-(x64|arm64)\.exe$/i.test(name));
  if (!converter) throw new Error("unpacked export converter is missing");
  inspectPeFile(
    path.join(converterRoot, converter),
    "unpacked export converter"
  );
  inspectPeFile(
    path.join(resources, "fastapi", "fastapi.exe"),
    "unpacked FastAPI executable"
  );
  requireFile(
    path.join(
      resources,
      "contracts",
      "authored_vnext",
      "ooxml",
      "v1",
      "generated",
      "ooxml_package.py"
    ),
    "unpacked authored OOXML contract",
    100
  );
  requireFile(
    path.join(
      resources,
      "servers",
      "nextjs",
      "lib",
      "template-v2-json-to-html.ts"
    ),
    "unpacked template package renderer evidence",
    100
  );
  const unpackedTemplateRegistry = path.join(
    resources,
    "templates",
    "package-registry.v1.json"
  );
  requireFile(unpackedTemplateRegistry, "unpacked template package registry", 100);
  const unpackedRegistry = JSON.parse(fs.readFileSync(unpackedTemplateRegistry, "utf8"));
  if (unpackedRegistry.legacyCompatibility?.enabled !== false) {
    throw new Error("unpacked template registry must keep legacyCompatibility.enabled=false");
  }
  requireFile(
    path.join(
      resources,
      "contracts",
      "template_package",
      "engines",
      "template-v2-json.v1.json"
    ),
    "unpacked template package engine",
    100
  );
  const direct = path.join(resources, "nextjs", "server.js");
  const nested = path.join(resources, "nextjs", "servers", "nextjs", "server.js");
  const server = fs.existsSync(direct) ? direct : nested;
  requireFile(server, "unpacked Next.js server.js");
  const serverRoot = path.dirname(server);
  requireDirectoryWithFiles(
    path.join(serverRoot, ".next-build", "static"),
    "unpacked Next.js static assets"
  );
  requireDirectoryWithFiles(
    path.join(serverRoot, "public"),
    "unpacked Next.js public assets"
  );
  return { executable, server, signature };
}

function validateWindowsRelease(options = {}) {
  const distRoot = path.resolve(options.distRoot || path.join(__dirname, "..", "dist"));
  const signatureCheck = options.signatureCheck || defaultSignatureCheck;
  const requireSignature = options.requireSignature === true;
  const expectedExportVersion =
    options.expectedExportVersion || readExpectedExportVersion(options);
  const artifacts = discoverArtifacts(distRoot);
  inspectPeFile(artifacts.installer, "NSIS installer");
  const unpackedExecutable = path.join(distRoot, "win-unpacked", "Presenton.exe");
  if (fs.existsSync(unpackedExecutable)) {
    inspectPeFile(unpackedExecutable, "unpacked Presenton.exe");
  }
  const signatures = {};
  if (requireSignature) {
    const signingTargets = [
      ["installerSignature", artifacts.installer, "NSIS installer"],
      ["appxSignature", artifacts.appx, "AppX package"],
    ];
    if (fs.existsSync(unpackedExecutable)) {
      signingTargets.push([
        "unpackedSignature",
        unpackedExecutable,
        "unpacked Presenton.exe",
      ]);
    }
    const signingErrors = [];
    for (const [key, filePath, label] of signingTargets) {
      try {
        signatures[key] = requireValidSignature(filePath, label, signatureCheck);
      } catch (error) {
        signingErrors.push(error.message);
      }
    }
    if (signingErrors.length > 0) {
      throw new Error(`Windows signing blockers: ${signingErrors.join("; ")}`);
    }
  }
  const appx = validateAppx(artifacts.appx, {
    ...options,
    requireSignature,
    expectedExportVersion,
  });
  const unpacked = validateUnpacked(
    distRoot,
    signatureCheck,
    signatures.unpackedSignature,
    requireSignature,
    expectedExportVersion
  );
  return { ...artifacts, appx, ...signatures, unpacked };
}

function main() {
  try {
    const args = process.argv.slice(2);
    const unknownFlags = args.filter(
      (arg) => arg.startsWith("-") && arg !== "--require-signature"
    );
    if (unknownFlags.length > 0) {
      throw new Error(`Unknown option: ${unknownFlags.join(", ")}`);
    }
    const paths = args.filter((arg) => !arg.startsWith("-"));
    if (paths.length > 1) throw new Error("Expected at most one dist path");
    const requireSignature = args.includes("--require-signature");
    const result = validateWindowsRelease({
      distRoot: paths[0] || undefined,
      requireSignature,
    });
    console.log("[windows-release-verify] OK");
    console.log(
      `  - mode: ${requireSignature ? "release/signing" : "structure (unsigned allowed)"}`
    );
    console.log(`  - NSIS: ${result.installer}`);
    console.log(`  - AppX: ${result.appx.version} ${result.appx.architecture}`);
    console.log(`  - Next.js: ${result.appx.nextServer}`);
  } catch (error) {
    console.error(`[windows-release-verify] ${error.message}`);
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main();
} else {
  module.exports = {
    APP_RESOURCE_PREFIX,
    EXPECTED_EXPORT_VERSION,
    EXPECTED_APPX_VERSION,
    EXPECTED_IDENTITY,
    EXPECTED_PUBLISHER,
    inspectPeBuffer,
    inspectPeFile,
    readExpectedAppxVersion,
    readExpectedExportVersion,
    readArchiveEntryPrefix,
    validateAppx,
    validateWindowsRelease,
    xmlTagAttributes,
  };
}
