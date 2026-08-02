import { defineConfig } from "cypress";
import path from "path";
import webpack from "webpack";

// framework "react" + a manual webpack pipeline. Cypress 14's framework:"next"
// devServer expects Next's compiled webpack (`webpackModule.init`), which Next 16
// (Turbopack) no longer ships — so the next-adapter throws. This minimal config
// bundles React/TSX component specs directly (alias + babel), which is all the
// component tests need.
export default defineConfig({
  // Cypress 15 deprecates exposing Cypress.env() to browser code. Component
  // specs use network fixtures rather than browser-visible secrets.
  allowCypressEnv: false,
  e2e: {
    // `npm run test:workspace-e2e` supplies CYPRESS_BASE_URL for the disposable
    // Next app it starts. Browser API traffic stays inside explicit route-spec
    // fixtures, so this suite needs neither port 5000 nor a FastAPI process.
    supportFile: false,
  },
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
        plugins: [
          // next/link and next/navigation read process.env.* at module load;
          // the isolated component-test bundle has no Node process, so importing
          // any next-dependent component otherwise fails with "process is not
          // defined" before a test can run. Shim process.env for the browser.
          new webpack.DefinePlugin({
            "process.env": JSON.stringify({ NODE_ENV: "development" }),
          }),
        ],
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
