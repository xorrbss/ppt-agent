import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { access, lstat, mkdir, mkdtemp, readFile, readdir, realpath, rename, rm, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { VIEWPORT, sha256 } from "./template-v2-pdf-golden-metrics.mjs";
export function textRangesOverflowContainer(containerRect, textRects, tolerance = 1) {
  return textRects.some((rect) => rect.left < containerRect.left - tolerance ||
    rect.top < containerRect.top - tolerance || rect.right > containerRect.right + tolerance ||
    rect.bottom > containerRect.bottom + tolerance);
}
export function clipsOverflow(values) {
  return values.some((value) => /^(?:auto|clip|hidden|scroll)$/i.test(value));
}
function normalizedRepoPath(value) {
  return value.replaceAll("\\", "/").replace(/^\.\//, "");
}
async function sha256File(filePath) {
  return sha256(await readFile(filePath));
}
export function spawnCapture(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      ...options,
    });
    const stdout = [];
    const stderr = [];
    child.stdout?.on("data", (chunk) => stdout.push(chunk));
    child.stderr?.on("data", (chunk) => stderr.push(chunk));
    child.once("error", reject);
    child.once("close", (code, signal) => {
      const result = {
        code,
        signal,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      };
      if (code === 0) resolve(result);
      else reject(new Error(`${command} exited ${code ?? signal}: ${result.stderr || result.stdout}`));
    });
  });
}
function firstExisting(candidates) {
  return candidates.filter(Boolean).find((candidate) => existsSync(candidate));
}
export function resolveChromeExecutable() {
  return firstExisting([
    process.env.PUPPETEER_EXECUTABLE_PATH,
    process.env.CHROME_PATH,
    process.platform === "win32"
      ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
      : undefined,
    process.platform === "win32"
      ? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
      : undefined,
    process.platform === "darwin"
      ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      : undefined,
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ]);
}
export function resolvePopplerExecutables() {
  const explicit = process.env.PDF_GOLDEN_PDFTOPPM_PATH?.trim();
  const bundledWindows = process.env.USERPROFILE
    ? path.join(
        process.env.USERPROFILE,
        ".cache",
        "codex-runtimes",
        "codex-primary-runtime",
        "dependencies",
        "native",
        "poppler",
        "Library",
        "bin",
        "pdftoppm.exe",
      )
    : undefined;
  const pdftoppm = firstExisting([
    explicit,
    bundledWindows,
    process.platform === "win32" ? undefined : "/usr/local/bin/pdftoppm",
    process.platform === "win32" ? undefined : "/opt/homebrew/bin/pdftoppm",
    process.platform === "win32" ? undefined : "/usr/bin/pdftoppm",
  ]);
  assert.ok(pdftoppm, "A real pdftoppm executable is required (set PDF_GOLDEN_PDFTOPPM_PATH)");
  assert.notEqual(path.extname(pdftoppm).toLowerCase(), ".cmd", "pdftoppm wrappers are not accepted");
  const executableExtension = process.platform === "win32" ? ".exe" : "";
  const pdfinfo = path.join(path.dirname(pdftoppm), `pdfinfo${executableExtension}`);
  assert.ok(existsSync(pdfinfo), `pdfinfo sibling is required: ${pdfinfo}`);
  return { pdftoppm, pdfinfo };
}

function portableToolPath(toolPath, repositoryRoot) {
  const relative = path.relative(repositoryRoot, toolPath);
  if (relative && !relative.startsWith("..") && !path.isAbsolute(relative)) {
    return normalizedRepoPath(relative);
  }
  const home = os.homedir();
  const homeRelative = path.relative(home, toolPath);
  if (homeRelative && !homeRelative.startsWith("..") && !path.isAbsolute(homeRelative)) {
    return `$USERPROFILE/${normalizedRepoPath(homeRelative)}`;
  }
  return normalizedRepoPath(toolPath);
}

function isStrictDescendant(root, candidate) {
  const relative = path.relative(root, candidate);
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

async function rejectExistingLinks(root, target) {
  const relative = path.relative(root, target);
  let current = root;
  for (const segment of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    try {
      const info = await lstat(current);
      assert.equal(info.isSymbolicLink(), false, `gate path traverses a link/reparse point: ${current}`);
    } catch (error) {
      if (error?.code === "ENOENT") break;
      throw error;
    }
  }
}

export async function validateGatePaths({ repositoryRoot, artifactRoot, receiptPath }) {
  const repository = await realpath(repositoryRoot);
  const artifactBoundary = path.join(repository, "test-artifacts");
  const artifact = path.resolve(artifactRoot);
  assert.ok(
    isStrictDescendant(artifactBoundary, artifact),
    `artifactRoot must be a gate-specific directory below ${artifactBoundary}`,
  );
  const artifactRelative = path.relative(artifactBoundary, artifact);
  assert.match(
    artifactRelative.split(path.sep)[0],
    /^template-v2-pdf-golden(?:-|$)/,
    "artifactRoot must use the template-v2-pdf-golden namespace",
  );
  const receiptBoundary = path.join(repository, "templates", "manifests");
  const receipt = path.resolve(receiptPath);
  assert.equal(path.dirname(receipt), receiptBoundary, "receiptPath must be directly under templates/manifests");
  assert.equal(path.basename(receipt), "template-v2.pdf-golden.v1.json", "unexpected PDF golden receipt name");
  await rejectExistingLinks(repository, artifact);
  await rejectExistingLinks(repository, receipt);
  return { repository, artifactRoot: artifact, receiptPath: receipt };
}

export async function writeJsonAtomic(destination, value) {
  const temporary = path.join(
    path.dirname(destination),
    `.${path.basename(destination)}.${process.pid}.${Date.now()}.tmp`,
  );
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
  try {
    await rename(temporary, destination);
  } catch (error) {
    await rm(temporary, { force: true });
    throw error;
  }
}

export async function toolProvenance(toolPath, versionArgs, repositoryRoot) {
  const version = await spawnCapture(toolPath, versionArgs);
  const file = await stat(toolPath);
  return {
    path: portableToolPath(toolPath, repositoryRoot),
    version: `${version.stdout}\n${version.stderr}`.trim().split(/\r?\n/)[0],
    sha256: await sha256File(toolPath),
    sizeBytes: file.size,
  };
}

export async function chromeProvenance(toolPath, repositoryRoot) {
  const profile = await mkdtemp(path.join(os.tmpdir(), "pdf-golden-chrome-version-"));
  const child = spawn(toolPath, [
    "--headless=new",
    "--disable-gpu",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "about:blank",
  ], { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
  try {
    const endpoint = await new Promise((resolve, reject) => {
      let stderr = "";
      const timeout = setTimeout(() => reject(new Error(`Chrome CDP version probe timed out: ${stderr.slice(-500)}`)), 15_000);
      child.stderr.on("data", (chunk) => {
        stderr = `${stderr}${chunk}`.slice(-4_000);
        const match = /DevTools listening on (ws:\/\/[^\s]+)/.exec(stderr);
        if (match) { clearTimeout(timeout); resolve(new URL(match[1])); }
      });
      child.once("error", reject);
      child.once("exit", (code) => reject(new Error(`Chrome version probe exited ${code}`)));
    });
    const version = await fetch(`http://${endpoint.host}/json/version`).then((response) => response.json());
    const cli = await spawnCapture(toolPath, ["--version"]).catch((error) => ({ stdout: "", stderr: String(error) }));
    const file = await stat(toolPath);
    return {
      path: portableToolPath(toolPath, repositoryRoot),
      product: version.Browser,
      userAgent: version["User-Agent"],
      executableSha256: await sha256File(toolPath),
      sizeBytes: file.size,
      cliVersionDiagnostic: `${cli.stdout ?? ""}\n${cli.stderr ?? ""}`.trim().slice(0, 500),
    };
  } finally {
    child.kill();
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
}

export async function fontProvenance(repositoryRoot, templateId) {
  const staticRoot = path.join(repositoryRoot, "templates", templateId, "static");
  const names = (await readdir(staticRoot)).filter((name) => /\.(?:ttf|otf)$/i.test(name)).sort();
  assert.ok(names.length > 0, `${templateId} must provide local fonts`);
  const files = [];
  for (const name of names) {
    const filePath = path.join(staticRoot, name);
    files.push({
      path: normalizedRepoPath(path.relative(repositoryRoot, filePath)),
      sha256: await sha256File(filePath),
      sizeBytes: (await stat(filePath)).size,
    });
  }
  return { templateId, files, aggregateSha256: sha256(Buffer.from(files.map((file) => file.sha256).join("\n"))) };
}

function telemetryScript() {
  return String.raw`
    (async()=>{
      const textRangesOverflowContainer=${textRangesOverflowContainer.toString()};
      const clipsOverflow=${clipsOverflow.toString()};
      if(document.readyState==='loading') await new Promise(resolve=>document.addEventListener('DOMContentLoaded',resolve,{once:true}));
      const pages=[...document.querySelectorAll('section[data-pdf-golden-page]')];
      if(pages.length===0) throw new Error('PDF golden selection has no pages');
      const deadline=Date.now()+20000;
      await Promise.all([...document.images].map(image=>{
        if(image.complete){
          if(image.naturalWidth>0) return null;
          return Promise.reject(new Error('required image completed without pixels'));
        }
        return new Promise((resolve,reject)=>{
          const timeout=setTimeout(()=>reject(new Error('required image readiness timed out')),Math.max(1,deadline-Date.now()));
          image.addEventListener('load',()=>{clearTimeout(timeout);image.naturalWidth>0?resolve():reject(new Error('required image loaded without pixels'))},{once:true});
          image.addEventListener('error',()=>{clearTimeout(timeout);reject(new Error('required image failed'))},{once:true});
        });
      }));
      await document.fonts?.ready;
      const fontFaces=[...(document.fonts||[])];
      await Promise.all(fontFaces.map(face=>face.load().catch(()=>{})));
      if(document.querySelector('canvas[data-presenton-chart]')){
        while(Date.now()<deadline&&window.__PRESENTON_JSON_CHARTS__?.status!=='ready'){
          if(window.__PRESENTON_JSON_CHARTS__?.status==='error') throw new Error('chart render failed');
          await new Promise(resolve=>setTimeout(resolve,25));
        }
        if(window.__PRESENTON_JSON_CHARTS__?.status!=='ready') throw new Error('chart readiness timed out');
      }
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>setTimeout(resolve,100))));
      const layouts=pages.map((page,index)=>{
        const pageRect=page.getBoundingClientRect();
        const boxes=[];
        const walker=document.createTreeWalker(page,NodeFilter.SHOW_TEXT);
        for(let node=walker.nextNode();node;node=walker.nextNode()){
          if(!node.textContent?.trim()) continue;
          const range=document.createRange(); range.selectNodeContents(node);
          for(const rect of range.getClientRects()){
            if(rect.width>0&&rect.height>0) boxes.push({x:rect.x-pageRect.x,y:rect.y-pageRect.y,width:rect.width,height:rect.height});
          }
        }
        let overflowElements=0,clippedText=0,transparencyCount=0;
        for(const element of page.querySelectorAll('*')){
          const style=getComputedStyle(element);
          const directText=[...element.childNodes].filter(node=>node.nodeType===Node.TEXT_NODE&&node.textContent?.trim());
          const textRects=directText.flatMap(node=>{const range=document.createRange();range.selectNodeContents(node);return [...range.getClientRects()];});
          let container=element,clips=false;
          while(container!==page){const candidateStyle=getComputedStyle(container);clips=clipsOverflow([candidateStyle.overflow,candidateStyle.overflowX,candidateStyle.overflowY]);if(clips)break;container=container.parentElement;}
          const overflow=textRangesOverflowContainer(container.getBoundingClientRect(),textRects);
          if(overflow) overflowElements+=1;
          if(overflow&&clips) clippedText+=1;
          if(Number(style.opacity)<0.999||style.mixBlendMode!=='normal'||style.filter!=='none') transparencyCount+=1;
        }
        return {index,textBoxes:boxes,fontSetStatus:document.fonts?.status||'unsupported',failedFonts:fontFaces.filter(font=>font.status!=='loaded').length,overflowElements,clippedText,transparencyCount,svgCount:page.querySelectorAll('svg').length,chartCanvasCount:page.querySelectorAll('canvas[data-presenton-chart]').length};
      });
      await fetch('/telemetry',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({layouts})});
      const marker=document.createElement('div');
      marker.hidden=true;
      marker.dataset.renderArtifactContract='pdf-golden';
      marker.dataset.renderArtifactTarget='pdf';
      marker.dataset.renderArtifactVersion='pdf-golden/revision-1';
      document.body.append(marker);
      document.documentElement.dataset.templateV2RenderReady='true';
      document.documentElement.dataset.templateV2RenderState='ready';
    })().catch(error=>{
      document.documentElement.dataset.templateV2ResourceError='true';
      document.documentElement.dataset.templateV2ResourceErrorCode='pdf-golden-proxy-failed';
      document.documentElement.dataset.templateV2RenderState='error';
      console.error(error);
    });
  `;
}

function selectionHtml(layouts, layoutHtmlDocuments) {
  const styles = [...new Set(layoutHtmlDocuments.flatMap((html) => html.match(/<style\b[^>]*>[\s\S]*?<\/style>/gi) ?? []))].join("");
  const pages = layoutHtmlDocuments
    .map((html, index) => {
      const body = /<body[^>]*>([\s\S]*?)<\/body>/i.exec(html)?.[1];
      assert.ok(body, `layout ${layouts[index].layoutId} has no HTML body`);
      const content = body.split(/<script\b/i, 1)[0];
      return `<section data-pdf-golden-page="${index}" data-layout-id=${JSON.stringify(layouts[index].layoutId)}>${content}</section>`;
    })
    .join("");
  const chartScripts = [...new Set(layoutHtmlDocuments.flatMap((html) => {
    if (!html.includes("data-presenton-chart")) return [];
    return (html.match(/<script\b[^>]*>[\s\S]*?<\/script>/gi) ?? []).filter(
      (script) =>
        !script.includes("data-presenton-template-v2-resource-contract") &&
        !script.includes("data-presenton-template-v2-readiness"),
    );
  }))].join("");
  return `<!doctype html><html><head><meta charset="utf-8">${styles}<style>@page{size:1280px 720px;margin:0}*{box-sizing:border-box}html,body{width:1280px;height:auto!important;margin:0;padding:0;overflow:visible!important;background:#fff}section[data-pdf-golden-page]{display:block;position:relative;width:1280px;height:720px;overflow:hidden;contain:layout paint size style;isolation:isolate;background:#fff;break-after:page;page-break-after:always}section[data-pdf-golden-page]:last-of-type{break-after:auto;page-break-after:auto}</style></head><body>${pages}${chartScripts}<script data-presenton-template-v2-readiness>${telemetryScript()}</script></body></html>`;
}

export async function startGoldenServer(repositoryRoot, templateId) {
  const script = path.join(
    repositoryRoot,
    "servers",
    "nextjs",
    "lib",
    "editorial-template-browser-golden.test.mjs",
  );
  const child = spawn(process.execPath, [script, "--serve-golden"], {
    cwd: path.join(repositoryRoot, "servers", "nextjs"),
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      TEMPLATE_PACKAGE_ID: templateId,
      TEMPLATE_PACKAGE_GOLDEN_PORT: "43127",
    },
  });
  const ready = new Promise((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => reject(new Error(`${templateId} golden server timeout: ${stderr}`)), 20_000);
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      if (stdout.includes("TEMPLATE_PACKAGE_GOLDEN_URL=")) {
        clearTimeout(timer);
        resolve();
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code !== null) reject(new Error(`${templateId} golden server exited ${code}: ${stderr}`));
    });
  });
  await ready;
  return {
    async close() {
      if (child.exitCode === null) child.kill();
      await new Promise((resolve) => {
        if (child.exitCode !== null) resolve();
        else {
          child.once("exit", resolve);
          setTimeout(resolve, 3_000).unref();
        }
      });
      if (child.exitCode === null && process.platform === "win32" && child.pid) {
        await spawnCapture("taskkill", ["/pid", String(child.pid), "/T", "/F"]).catch(() => {});
      }
    },
  };
}

export async function startSelectionProxy(layouts) {
  let telemetry = null;
  const layoutHtmlDocuments = await Promise.all(
    layouts.map(async (layout) => {
      const response = await fetch(`http://127.0.0.1:43127/layout/${layout.index}`);
      assert.equal(response.status, 200, `layout proxy failed for ${layout.layoutId}`);
      return response.text();
    }),
  );
  const html = selectionHtml(layouts, layoutHtmlDocuments);
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url ?? "/", "http://127.0.0.1");
      if (url.pathname === "/" || url.pathname === "/selection") {
        response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
        response.end(html);
        return;
      }
      if (url.pathname === "/telemetry" && request.method === "POST") {
        const chunks = [];
        for await (const chunk of request) chunks.push(chunk);
        telemetry = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        response.writeHead(204).end();
        return;
      }
      const upstream = await fetch(`http://127.0.0.1:43127${url.pathname}${url.search}`);
      response.writeHead(upstream.status, {
        "content-type": upstream.headers.get("content-type") ?? "application/octet-stream",
        "cache-control": "no-store",
        "access-control-allow-origin": "*",
      });
      response.end(Buffer.from(await upstream.arrayBuffer()));
    } catch (error) {
      response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
      response.end(String(error));
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  return {
    url: `http://127.0.0.1:${address.port}/selection?render_contract=pdf-golden&format=pdf`,
    getTelemetry() {
      return telemetry;
    },
    async close() {
      const closed = new Promise((resolve) => server.close(resolve));
      server.closeAllConnections?.();
      await closed;
    },
  };
}

export function converterPath(repositoryRoot) {
  const extension = process.platform === "win32" ? ".exe" : "";
  return path.join(
    repositoryRoot,
    "presentation-export",
    "py",
    `convert-${process.platform}-${process.arch}${extension}`,
  );
}

export async function runPresentationExport({ repositoryRoot, url, title, workRoot, chrome }) {
  const exportRoot = path.join(repositoryRoot, "presentation-export");
  const entrypoint = path.join(exportRoot, "index.cjs");
  const converter = converterPath(repositoryRoot);
  await Promise.all([access(entrypoint), access(converter)]);
  const appData = path.join(workRoot, "app_data");
  await mkdir(appData, { recursive: true });
  const taskPath = path.join(workRoot, "export-task.json");
  await writeFile(taskPath, JSON.stringify({ type: "export", url, format: "pdf", title }));
  await spawnCapture(process.execPath, [entrypoint, taskPath], {
    cwd: repositoryRoot,
    env: {
      ...process.env,
      APP_DATA_DIRECTORY: appData,
      TEMP_DIRECTORY: workRoot,
      BUILT_PYTHON_MODULE_PATH: converter,
      PUPPETEER_EXECUTABLE_PATH: chrome,
    },
  });
  const response = JSON.parse(await readFile(taskPath.replace(/\.json$/, ".response.json"), "utf8"));
  let outputPath;
  if (typeof response.path === "string") outputPath = response.path;
  else if (typeof response.url === "string" && response.url.startsWith("file:")) outputPath = fileURLToPath(response.url);
  else if (typeof response.url === "string" && response.url.startsWith("/app_data/")) {
    outputPath = path.join(appData, response.url.slice("/app_data/".length));
  }
  assert.ok(outputPath, "presentation-export did not return a PDF path");
  assert.equal((await readFile(outputPath)).subarray(0, 5).toString("ascii"), "%PDF-");
  return outputPath;
}

function numericPage(pathname) {
  return Number(/-(\d+)\.png$/i.exec(pathname)?.[1] ?? Number.NaN);
}

export async function renderPdfPages({ pdftoppm, pdfPath, outputRoot, expectedPages }) {
  await mkdir(outputRoot, { recursive: true });
  const prefix = path.join(outputRoot, "page");
  await spawnCapture(pdftoppm, ["-png", "-r", String(VIEWPORT.dpi), pdfPath, prefix]);
  const pages = (await readdir(outputRoot))
    .filter((name) => /^page-\d+\.png$/i.test(name))
    .map((name) => path.join(outputRoot, name))
    .sort((left, right) => numericPage(left) - numericPage(right));
  assert.equal(pages.length, expectedPages, `Poppler rendered ${pages.length}/${expectedPages} pages`);
  return pages;
}
