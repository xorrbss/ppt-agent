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
  linux: {
    target: ["AppImage"],
    icon: "resources/ui/assets/images/presenton_short_filled.png",
    files: [
      "dependencies/chrome-headless-shell/linux_build",
    ]
  },
  win: {
    target: ["portable"],
    icon: "resources/ui/assets/images/presenton_short_filled.png",
    files: [
      "dependencies/chrome-headless-shell/win_build",
    ]
  },
}


builder.build({ config })