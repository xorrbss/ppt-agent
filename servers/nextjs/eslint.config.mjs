import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["**/*.{js,jsx,mjs,ts,tsx,mts,cts}"],
    // Preserve the existing lint baseline while moving off the removed `next lint` command.
    rules: {
      "@typescript-eslint/no-empty-object-type": "off",
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-namespace": "off",
      "@typescript-eslint/no-non-null-asserted-optional-chain": "off",
      "@typescript-eslint/no-require-imports": "off",
      "@next/next/no-assign-module-variable": "off",
      "prefer-const": "off",
      "react-hooks/immutability": "off",
      "react-hooks/preserve-manual-memoization": "off",
      "react-hooks/purity": "off",
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/static-components": "off",
      "react-hooks/use-memo": "off",
      "react/jsx-key": "off",
      "react/no-unescaped-entities": "off",
    },
  },
  {
    files: ["**/*.cjs"],
    rules: { "@typescript-eslint/no-require-imports": "off" },
  },
  {
    // Authored is frozen; preserve its existing render contract until that flow can be revised.
    files: [
      "app/(presentation-generator)/authored-vnext/AuthoredVnextFlowClient.tsx",
      "app/(presentation-generator)/authored-vnext/master-setup/MasterCatalogDialog.tsx",
    ],
    rules: { "react-hooks/refs": "off" },
  },
  globalIgnores([
    ".next/**",
    ".next-build/**",
    ".next-slidespec-dev/**",
    ".next-workspace-e2e/**",
    "out/**",
    "build/**",
    "cypress/**",
    "public/vendor/**",
    "next-env.d.ts",
    "**/*.cy.ts",
    "**/*.cy.tsx",
  ]),
]);

export default eslintConfig;
