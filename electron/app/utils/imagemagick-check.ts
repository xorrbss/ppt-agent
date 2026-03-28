import { spawnSync } from "child_process";

function canExecute(command: string, args: string[]): boolean {
  const result = spawnSync(command, args, {
    stdio: "pipe",
    windowsHide: true,
  });
  return result.status === 0;
}

export function isImageMagickInstalled(): boolean {
  // ImageMagick 7+ command
  if (canExecute("magick", ["-version"])) return true;
  // Legacy command on Linux/macOS packages
  if (canExecute("convert", ["-version"])) return true;
  return false;
}

export function getImageMagickDownloadUrl(): string {
  if (process.platform === "win32") {
    return "https://imagemagick.org/script/download.php#windows";
  }
  if (process.platform === "darwin") {
    return "https://imagemagick.org/script/download.php#macosx";
  }
  return "https://imagemagick.org/script/download.php#linux";
}
