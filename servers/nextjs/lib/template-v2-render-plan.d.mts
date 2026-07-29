// Declaration redirect: TypeScript pairs `.mjs` import specifiers with `.d.mts`,
// not `.d.ts`, so without this file `import ... from "./template-v2-render-plan.mjs"`
// silently resolves to `any` under allowJs. Keep the real declarations in
// template-v2-render-plan.d.ts (shared with extensionless imports).
export * from "./template-v2-render-plan";
