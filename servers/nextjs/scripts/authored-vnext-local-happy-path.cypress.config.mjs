import { defineConfig } from "cypress";

// Keep the disposable local acceptance spec outside the normal Cypress suite:
// its processes and three-slide opt-in are explicit operator choices.
export default defineConfig({
  allowCypressEnv: false,
  e2e: {
    supportFile: false,
    specPattern: "scripts/authored-vnext-local-happy-path.cy.ts",
  },
});
