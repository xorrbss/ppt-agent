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
    // Now that the app stylesheet actually applies, mounted components have
    // their real size. Cypress's 500x500 default is smaller than several of
    // them, which pushed controls out of view and failed clicks on geometry no
    // user meets. This is a desktop app; give the specs a desktop.
    viewportWidth: 1280,
    viewportHeight: 800,
    specPattern: [
      "cypress/component/**/*.cy.{js,jsx,ts,tsx}",
      "app/**/*.cy.{js,jsx,ts,tsx}",
      "components/**/*.cy.{js,jsx,ts,tsx}",
    ],
    devServer: {
      framework: "react",
      bundler: "webpack",
      webpackConfig: {
        mode: "development",
        devtool: false,
        // A one-shot run never hot-reloads, and the client that implements it
        // schedules with requestAnimationFrame but clears with clearTimeout.
        // Specs that install cy.clock() stub both, so the mismatch throws into
        // the spec as an application error. Nothing here needs the socket:
        // style-loader only registers a CSS hot-accept because it is present.
        devServer: { hot: false, liveReload: false, webSocketServer: false },
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
            // Real Tailwind, not a stub. Under `asset/source` a stylesheet
            // import became a discarded string, so utilities never applied and
            // specs failed on layout the app does not actually have: a
            // `pointer-events-none` preview image stayed clickable and swallowed
            // the button beneath it. postcss-loader picks up postcss.config.mjs.
            {
              test: /\.css$/,
              use: ["style-loader", "css-loader", "postcss-loader"],
            },
          ],
        },
      },
    },
  },
});
