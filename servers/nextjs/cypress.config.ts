import { defineConfig } from "cypress";
import path from "path";

// framework "react" + a manual webpack pipeline. Cypress 14's framework:"next"
// devServer expects Next's compiled webpack (`webpackModule.init`), which Next 16
// (Turbopack) no longer ships — so the next-adapter throws. This minimal config
// bundles React/TSX component specs directly (alias + babel), which is all the
// component tests need.
export default defineConfig({
  component: {
    devServer: {
      framework: "react",
      bundler: "webpack",
      webpackConfig: {
        mode: "development",
        devtool: false,
        resolve: {
          extensions: [".tsx", ".ts", ".jsx", ".js", ".json"],
          alias: { "@": path.resolve(process.cwd()) },
        },
        module: {
          rules: [
            {
              test: /\.[jt]sx?$/,
              exclude: /node_modules/,
              use: {
                loader: "babel-loader",
                options: {
                  presets: [
                    ["@babel/preset-env", { targets: { esmodules: true } }],
                    ["@babel/preset-react", { runtime: "automatic" }],
                    "@babel/preset-typescript",
                  ],
                },
              },
            },
            { test: /\.css$/, type: "asset/source" },
          ],
        },
      },
    },
  },
});
