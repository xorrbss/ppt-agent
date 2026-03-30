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
    return "https://imagemagick.org/archive/binaries/ImageMagick-7.1.2-18-Q16-HDRI-x64-dll.exe";
  }
  if (process.platform === "darwin") {
    return "https://brew.sh/";
  }
  return "https://imagemagick.org/script/download.php#linux";
}

export function getImageMagickManualInstallCommands(): string[] {
  if (process.platform === "win32") {
    return [
      "Download and run the installer:",
      getImageMagickDownloadUrl(),
    ];
  }

  if (process.platform === "darwin") {
    return [
      "Install Homebrew:",
      '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
      "Install ImageMagick:",
      "brew install imagemagick",
    ];
  }

  return [
    "Install ImageMagick:",
    "sudo apt-get update",
    "sudo apt-get install -y imagemagick",
  ];
}
