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
  ],
  linux: {
    target: ["AppImage"],
    desktop: {
      Name: "Presenton",
      Icon: "resources/ui/assets/images/presenton_short_filled.png",
      Categories: ["Utility"],
    },
  },
}


builder.build({ config })