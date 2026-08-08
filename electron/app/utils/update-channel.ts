const RELEASES_URL = "https://github.com/xorrbss/ppt-agent/releases/latest";
const RELEASE_PATH_PREFIX = "/xorrbss/ppt-agent/releases/download/";
const VERSION_PATTERN =
  /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/;

type Platform = NodeJS.Platform;

export interface UpdateDownloads {
  linux?: string;
  mac?: string;
  windows?: string;
}

interface ParsedVersion {
  core: [number, number, number];
  prerelease: string[] | null;
}

function parseVersion(version: string): ParsedVersion | null {
  const match = VERSION_PATTERN.exec(version);
  if (!match) return null;

  const core = [Number(match[1]), Number(match[2]), Number(match[3])] as [
    number,
    number,
    number,
  ];
  if (core.some((part) => !Number.isSafeInteger(part))) return null;

  return {
    core,
    prerelease: match[4]?.split(".") || null,
  };
}

export function isValidReleaseVersion(version: string): boolean {
  return parseVersion(version) !== null;
}

function comparePrerelease(left: string[] | null, right: string[] | null): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;

  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const leftPart = left[index];
    const rightPart = right[index];
    if (leftPart === undefined) return -1;
    if (rightPart === undefined) return 1;
    if (leftPart === rightPart) continue;

    const leftNumeric = /^\d+$/.test(leftPart);
    const rightNumeric = /^\d+$/.test(rightPart);
    if (leftNumeric && rightNumeric) return Number(leftPart) > Number(rightPart) ? 1 : -1;
    if (leftNumeric !== rightNumeric) return leftNumeric ? -1 : 1;
    return leftPart > rightPart ? 1 : -1;
  }
  return 0;
}

export function isNewerVersion(current: string, remote: string): boolean {
  const currentVersion = parseVersion(current);
  const remoteVersion = parseVersion(remote);
  if (!currentVersion || !remoteVersion) return false;

  for (let index = 0; index < currentVersion.core.length; index += 1) {
    if (remoteVersion.core[index] > currentVersion.core[index]) return true;
    if (remoteVersion.core[index] < currentVersion.core[index]) return false;
  }

  return comparePrerelease(remoteVersion.prerelease, currentVersion.prerelease) > 0;
}

export function getUpdateDownloadUrl(
  version: string,
  downloads: UpdateDownloads | undefined,
  platform: Platform
): string {
  if (!isValidReleaseVersion(version)) return RELEASES_URL;

  const candidate =
    platform === "win32"
      ? downloads?.windows
      : platform === "darwin"
        ? downloads?.mac
        : downloads?.linux;
  if (!candidate) return RELEASES_URL;

  try {
    const url = new URL(candidate);
    const expectedPath = `${RELEASE_PATH_PREFIX}electron-v${version}/`;
    if (
      url.protocol !== "https:" ||
      url.hostname !== "github.com" ||
      url.username ||
      url.password ||
      !url.pathname.startsWith(expectedPath)
    ) {
      return RELEASES_URL;
    }
    return url.toString();
  } catch {
    return RELEASES_URL;
  }
}
