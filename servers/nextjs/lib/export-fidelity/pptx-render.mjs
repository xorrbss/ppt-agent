import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.once("error", reject);
    child.once("close", (code) => {
      if (code === 0) {
        resolve({ stdout: Buffer.concat(stdout), stderr: Buffer.concat(stderr) });
        return;
      }
      reject(new Error(`${command} exited ${code}: ${Buffer.concat(stderr).toString("utf8").trim()}`));
    });
  });
}

async function executableOnPath(command) {
  try {
    const lookup = process.platform === "win32" ? "where" : "which";
    const result = await run(lookup, [command]);
    return result.stdout.toString("utf8").split(/\r?\n/).map((line) => line.trim()).find(Boolean);
  } catch {
    return undefined;
  }
}

async function firstExecutable(candidates) {
  for (const candidate of candidates.filter(Boolean)) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // Candidate can be a command name resolved via PATH below.
    }
  }
  return undefined;
}

export async function resolvePptxRenderTools() {
  const programFiles = process.env.ProgramFiles || "C:\\Program Files";
  const configuredSoffice = process.env.SOFFICE_PATH?.trim();
  const configuredPdfToCairo = process.env.PDFTOCAIRO_PATH?.trim();
  const soffice =
    (await firstExecutable([
      configuredSoffice,
      path.join(programFiles, "LibreOffice", "program", "soffice.exe"),
    ])) ??
    (configuredSoffice ? undefined : await executableOnPath(process.platform === "win32" ? "soffice.exe" : "soffice"));
  const pdftocairo =
    (await firstExecutable([configuredPdfToCairo])) ??
    (configuredPdfToCairo ? undefined : await executableOnPath(process.platform === "win32" ? "pdftocairo.exe" : "pdftocairo"));
  return { soffice, pdftocairo };
}

/**
 * Render each requested PPTX page through pinned LibreOffice and Poppler.
 * ImageMagick is intentionally not used: its PDF delegate/policy varies across
 * Windows installations and would make a visual CI gate non-reproducible.
 */
export async function renderPptxToPngPages({
  pptxPath,
  outputDirectory,
  pageCount,
  tools,
}) {
  if (!tools?.soffice || !tools?.pdftocairo) {
    throw new Error("PPTX fidelity rendering requires both SOFFICE_PATH and PDFTOCAIRO_PATH (or both commands on PATH).");
  }
  await fs.mkdir(outputDirectory, { recursive: true });
  const profileDirectory = path.join(outputDirectory, "libreoffice-profile");
  await fs.mkdir(profileDirectory, { recursive: true });
  const pdfDirectory = path.join(outputDirectory, "pdf");
  await fs.mkdir(pdfDirectory, { recursive: true });
  await run(tools.soffice, [
    `-env:UserInstallation=${pathToFileURL(profileDirectory + path.sep).href}`,
    "--headless",
    "--nologo",
    "--nodefault",
    "--nolockcheck",
    "--norestore",
    "--convert-to",
    "pdf",
    "--outdir",
    pdfDirectory,
    pptxPath,
  ]);
  const pdfPath = path.join(pdfDirectory, `${path.basename(pptxPath, path.extname(pptxPath))}.pdf`);
  await fs.access(pdfPath);
  const pages = [];
  for (let page = 1; page <= pageCount; page += 1) {
    const outputBase = path.join(outputDirectory, `slide-${page}`);
    await run(tools.pdftocairo, [
      "-png",
      "-singlefile",
      "-f", String(page),
      "-l", String(page),
      "-scale-to-x", "1280",
      "-scale-to-y", "720",
      pdfPath,
      outputBase,
    ]);
    const pngPath = `${outputBase}.png`;
    await fs.access(pngPath);
    pages.push(await fs.readFile(pngPath));
  }
  return pages;
}
