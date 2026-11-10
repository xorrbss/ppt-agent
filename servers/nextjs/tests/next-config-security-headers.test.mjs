import assert from "node:assert/strict";
import test from "node:test";

import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match.js";

import nextConfig, {
  appPageContentSecurityPolicyReportOnly,
  appPageCspSource,
  buildAppPageContentSecurityPolicyReportOnly,
  fastApiOriginForCsp,
  securityHeaders,
} from "../next.config.mjs";

const REQUIRED_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "SAMEORIGIN",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
};

// Hand-authored golden expectation for the default (no NEXT_PUBLIC_FAST_API)
// policy, independent of next.config.mjs's own construction — this is what
// actually catches a content regression, unlike comparing the header back to
// the same in-memory string next.config.mjs put there.
const EXPECTED_DEFAULT_CSP_DIRECTIVES = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com",
  "img-src 'self' data: blob: http: https:",
  "media-src 'self' data: blob:",
  "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com",
  "worker-src 'self' blob:",
  "frame-src 'self' blob: data:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'self'",
  "report-uri /api/csp-report",
].join("; ");

function headerMap(entries) {
  return Object.fromEntries(entries.map(({ key, value }) => [key, value]));
}

test("every route receives the application security header set", async () => {
  const rules = await nextConfig.headers();
  const global = rules.find((rule) => rule.source === "/:path*");
  assert.ok(global, "a catch-all header rule is required");
  assert.deepEqual(headerMap(global.headers), REQUIRED_HEADERS);
  assert.deepEqual(headerMap(securityHeaders), REQUIRED_HEADERS);
  // SAMEORIGIN, never DENY: the editor's slide-preview iframes are
  // same-origin (isolated by their own <meta> CSP, not cross-origin
  // framing), so DENY would break them. /p/ share pages are opened directly
  // via the share link and are not framed by anything in this codebase.
  assert.notEqual(headerMap(global.headers)["X-Frame-Options"], "DENY");
});

test("application pages get a report-only CSP matching a hand-authored golden policy", async () => {
  const rules = await nextConfig.headers();
  const csp = rules.find((rule) => rule.source === appPageCspSource);
  assert.ok(csp, "the app-page CSP rule is required");
  const headers = headerMap(csp.headers);
  assert.deepEqual(Object.keys(headers), ["Content-Security-Policy-Report-Only"]);

  // In this test process NEXT_PUBLIC_FAST_API is unset, so the exported
  // constant (computed once at module load) must equal the default policy.
  assert.equal(process.env.NEXT_PUBLIC_FAST_API, undefined);
  assert.equal(
    headers["Content-Security-Policy-Report-Only"],
    EXPECTED_DEFAULT_CSP_DIRECTIVES,
  );
  assert.equal(appPageContentSecurityPolicyReportOnly, EXPECTED_DEFAULT_CSP_DIRECTIVES);

  assert.doesNotMatch(
    appPageContentSecurityPolicyReportOnly,
    /frame-ancestors 'none'/u,
  );
  // No enforced CSP anywhere in next.config: renderer/export surfaces emit
  // their own per-response policy.
  for (const rule of rules) {
    for (const { key } of rule.headers) {
      assert.notEqual(key, "Content-Security-Policy");
    }
  }
});

test("report-uri sends violations somewhere instead of console-only noise", () => {
  assert.match(appPageContentSecurityPolicyReportOnly, /report-uri \/api\/csp-report\b/u);
});

test("img-src allows http for local providers such as a self-hosted ComfyUI", () => {
  assert.match(appPageContentSecurityPolicyReportOnly, /img-src[^;]*\bhttp:/u);
  assert.match(appPageContentSecurityPolicyReportOnly, /img-src[^;]*\bhttps:/u);
});

test("connect-src stays self-only when NEXT_PUBLIC_FAST_API is unset", () => {
  assert.equal(fastApiOriginForCsp(undefined), null);
  assert.equal(fastApiOriginForCsp(""), null);
  assert.equal(fastApiOriginForCsp("not a url"), null);
  const csp = buildAppPageContentSecurityPolicyReportOnly(fastApiOriginForCsp(undefined));
  assert.equal(csp, EXPECTED_DEFAULT_CSP_DIRECTIVES);
});

test("connect-src admits the FastAPI origin (EventSource included) when it differs", () => {
  const origin = fastApiOriginForCsp("http://backend-host:8000/");
  assert.equal(origin, "http://backend-host:8000");
  const csp = buildAppPageContentSecurityPolicyReportOnly(origin);
  const connectSrc = csp.split("; ").find((d) => d.startsWith("connect-src"));
  assert.ok(connectSrc, "connect-src directive must be present");
  assert.match(connectSrc, /(^|\s)http:\/\/backend-host:8000(\s|$)/u);
  // Everything else stays identical to the default policy.
  assert.equal(
    csp.replace(connectSrc, "connect-src 'self'"),
    EXPECTED_DEFAULT_CSP_DIRECTIVES.replace(
      /connect-src[^;]*/u,
      "connect-src 'self'",
    ),
  );
});

test("the report-only CSP skips surfaces that emit their own CSP, via Next's own route matcher", () => {
  // Use the exact matcher Next.js compiles `source` into at request time
  // (next/dist/shared/lib/router/utils/path-match's getPathMatch, the same
  // function next/dist/server/lib/router-utils/filesystem.js calls to build
  // custom-route matchers) instead of re-parsing the source string with a
  // hand-rolled `new RegExp`, which would only ever test the test's own
  // understanding of the pattern, not Next's.
  const matcher = getPathMatch(appPageCspSource);
  for (const excluded of [
    "/p/share-token",
    "/api/v1/ppt/presentation/all",
    "/api/user-config",
    "/api/csp-report",
    "/_next/static/chunks/main.js",
    "/static/icons/x.svg",
    "/app_data/images/x.png",
    "/favicon.ico",
    "/Logo.png",
  ]) {
    assert.equal(!!matcher(excluded), false, `${excluded} must be excluded`);
  }
  for (const included of [
    "/",
    "/upload",
    "/dashboard",
    "/presentation",
    "/presentation/workspace",
    "/settings",
    // The removed headless export renderer route is no longer exempt.
    "/pdf-maker",
  ]) {
    assert.equal(!!matcher(included), true, `${included} must be governed`);
  }
});
