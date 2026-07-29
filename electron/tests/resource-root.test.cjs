const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

function loadResourceRootModule() {
  const filename = path.resolve(__dirname, "../app/utils/resource-root.ts");
  const source = fs.readFileSync(filename, "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
    fileName: filename,
  }).outputText;
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(compiled, filename);
  return loaded.exports;
}

const { resolveResourcePath, resolveResourceRoot } = loadResourceRootModule();

test("default behavior preserves app.getAppPath in dev and packaged builds", () => {
  const appPath = path.resolve("C:/presenton/app");

  assert.equal(
    resolveResourceRoot({ isPackaged: false, appPath }),
    appPath
  );
  assert.equal(
    resolveResourceRoot({ isPackaged: true, appPath }),
    appPath
  );
});

test("phase-two opt-in resolves an external runtime below resourcesPath", () => {
  const appPath = path.resolve("C:/presenton/resources/app.asar");
  const resourcesPath = path.resolve("C:/presenton/resources");

  assert.equal(
    resolveResourceRoot({
      isPackaged: true,
      appPath,
      resourcesPath,
      externalResourceDirectory: "presenton-runtime",
    }),
    path.join(resourcesPath, "presenton-runtime")
  );
});

test("resource paths cannot escape the selected root", () => {
  const root = path.resolve("C:/presenton/resources/presenton-runtime");

  assert.equal(
    resolveResourcePath(root, "resources", "fastapi"),
    path.join(root, "resources", "fastapi")
  );
  assert.throws(
    () => resolveResourcePath(root, "..", "outside"),
    /must stay below resourceRoot/
  );
});

test("external resource directory rejects absolute and parent paths", () => {
  const options = {
    isPackaged: true,
    appPath: path.resolve("C:/presenton/resources/app.asar"),
    resourcesPath: path.resolve("C:/presenton/resources"),
  };

  assert.throws(
    () =>
      resolveResourceRoot({
        ...options,
        externalResourceDirectory: "../outside",
      }),
    /must stay below resourcesPath/
  );
  assert.throws(
    () =>
      resolveResourceRoot({
        ...options,
        externalResourceDirectory: path.resolve("C:/outside"),
      }),
    /must stay below resourcesPath/
  );
});
