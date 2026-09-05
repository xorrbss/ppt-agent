import assert from "node:assert/strict";
import test from "node:test";

import nextConfig, {
  appPageContentSecurityPolicyReportOnly,
  appPageCspSource,
  securityHeaders,
} from "../next.config.mjs";

const REQUIRED_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "SAMEORIGIN",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
};

function headerMap(entries) {
  return Object.fromEntries(entries.map(({ key, value }) => [key, value]));
}

test("every route receives the application security header set", async () => {
  const rules = await nextConfig.headers();
  const global = rules.find((rule) => rule.source === "/:path*");
  assert.ok(global, "a catch-all header rule is required");
  assert.deepEqual(headerMap(global.headers), REQUIRED_HEADERS);
  assert.deepEqual(headerMap(securityHeaders), REQUIRED_HEADERS);
  // SAMEORIGIN, never DENY: previews are same-origin sandboxed iframes and
  // /p/ share pages may be embedded.
  assert.notEqual(headerMap(global.headers)["X-Frame-Options"], "DENY");
});

test("application pages get a report-only CSP that allows what the app loads", async () => {
  const rules = await nextConfig.headers();
  const csp = rules.find((rule) => rule.source === appPageCspSource);
  assert.ok(csp, "the app-page CSP rule is required");
  const headers = headerMap(csp.headers);
  assert.deepEqual(Object.keys(headers), ["Content-Security-Policy-Report-Only"]);
  assert.equal(
    headers["Content-Security-Policy-Report-Only"],
    appPageContentSecurityPolicyReportOnly,
  );
  assert.doesNotMatch(
    appPageContentSecurityPolicyReportOnly,
    /frame-ancestors 'none'/u,
  );
  for (const directive of [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: blob: https:",
    "connect-src 'self' https://api-eu.mixpanel.com",
    "object-src 'none'",
  ]) {
    assert.ok(
      appPageContentSecurityPolicyReportOnly.includes(directive),
      `CSP must contain: ${directive}`,
    );
  }
  // No enforced CSP anywhere in next.config: renderer/export surfaces emit
  // their own per-response policy.
  for (const rule of rules) {
    for (const { key } of rule.headers) {
      assert.notEqual(key, "Content-Security-Policy");
    }
  }
});

test("the report-only CSP skips surfaces that emit their own CSP", () => {
  // path-to-regexp source: the custom group is a plain regular expression.
  const pattern = new RegExp(`^${appPageCspSource}$`, "u");
  for (const excluded of [
    "/pdf-maker",
    "/p/share-token",
    "/api/v1/ppt/presentation/all",
    "/api/user-config",
    "/_next/static/chunks/main.js",
    "/static/icons/x.svg",
    "/app_data/images/x.png",
    "/favicon.ico",
    "/Logo.png",
  ]) {
    assert.equal(pattern.test(excluded), false, `${excluded} must be excluded`);
  }
  for (const included of [
    "/",
    "/upload",
    "/dashboard",
    "/presentation",
    "/presentation/workspace",
    "/template-v2-studio/abc",
    "/settings",
  ]) {
    assert.equal(pattern.test(included), true, `${included} must be governed`);
  }
});
