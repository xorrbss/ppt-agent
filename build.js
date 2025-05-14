const builder = require("electron-builder")

const config = {
  appId: "ai.presenton",
  asar: false,
  directories: {
    output: "dist",
  },
  files: [
    "resources",
    "app_dist",
    "node_modules",
    "NOTICE",
  ],
  win: {
    target: ["nsis"],
    files: "dependencies/chrome-headless-shell/win_build",
    icon: "resources/ui/assets/images/presenton_short_filled.png"
  },
  nsis: {
    oneClick: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: "Presenton"
  }
}

builder.build({ config })