import path from "path";

export type ResourceRootOptions = {
  isPackaged: boolean;
  appPath: string;
  resourcesPath?: string;
  externalResourceDirectory?: string;
};

function requireAbsolutePath(value: string | undefined, label: string): string {
  if (!value || !path.isAbsolute(value)) {
    throw new Error(`${label} must be an absolute path`);
  }
  return path.resolve(value);
}

function requireRelativeDirectory(value: string): string {
  const normalized = path.normalize(value);
  if (
    !value.trim() ||
    path.isAbsolute(value) ||
    normalized === ".." ||
    normalized.startsWith(`..${path.sep}`)
  ) {
    throw new Error("externalResourceDirectory must stay below resourcesPath");
  }
  return normalized;
}

/**
 * Resolve the root that contains package.json, node_modules, and resources/.
 *
 * With no externalResourceDirectory this preserves the current app.getAppPath()
 * behavior, including packaged builds. ASAR phase two can opt in only after
 * electron-builder copies the same runtime tree below process.resourcesPath.
 */
export function resolveResourceRoot(options: ResourceRootOptions): string {
  const appRoot = requireAbsolutePath(options.appPath, "appPath");
  if (!options.isPackaged || !options.externalResourceDirectory) {
    return appRoot;
  }

  const resourcesRoot = requireAbsolutePath(
    options.resourcesPath,
    "resourcesPath"
  );
  return path.join(
    resourcesRoot,
    requireRelativeDirectory(options.externalResourceDirectory)
  );
}

export function resolveResourcePath(
  resourceRoot: string,
  ...segments: string[]
): string {
  const root = requireAbsolutePath(resourceRoot, "resourceRoot");
  const candidate = path.resolve(root, ...segments);
  const relative = path.relative(root, candidate);
  if (
    relative === ".." ||
    relative.startsWith(`..${path.sep}`) ||
    path.isAbsolute(relative)
  ) {
    throw new Error("resource path must stay below resourceRoot");
  }
  return candidate;
}
