import path from "node:path";
import { fileURLToPath } from "node:url";

const nextjsRoot = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(nextjsRoot, "../..");
const exportRuntimeTrace = ["../../presentation-export/**/*"];
const semverRuntimeTrace = [
  "node_modules/.pnpm/semver@*/node_modules/semver/**/*",
];
const sharpNativeRuntimeTrace = [
  "node_modules/@img/sharp-*/lib/**/*",
  "node_modules/.pnpm/@img+sharp-*/node_modules/@img/sharp-*/lib/**/*",
];
const linkFreeRuntimeTrace = [
  "node_modules/@img/**/*",
  "node_modules/@next/env/**/*",
  "node_modules/@swc/helpers/**/*",
  "node_modules/baseline-browser-mapping/**/*",
  "node_modules/caniuse-lite/**/*",
  "node_modules/client-only/**/*",
  "node_modules/detect-libc/**/*",
  "node_modules/nanoid/**/*",
  "node_modules/picocolors/**/*",
  "node_modules/postcss/**/*",
  "node_modules/scheduler/**/*",
  "node_modules/semver/**/*",
  "node_modules/source-map-js/**/*",
  "node_modules/styled-jsx/**/*",
];
const runtimeTailwindTrace = [
  // Tailwind reads this file with fs.readFileSync while constructing its core
  // plugins. Next's JS tracer cannot infer that runtime path for standalone.
  "node_modules/tailwindcss/lib/css/**/*",
];

// Application security headers. The same set is emitted by nginx.conf and
// scripts/presenton-local-proxy.mjs so direct :3000 access is covered too.
// Frame embedding stays SAMEORIGIN: slide previews render in same-origin
// sandboxed iframes and public share pages (/p/) may be embedded.
export const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

// Report-only CSP for application pages. Hosts are the ones the app actually
// loads: the shared Tailwind runtime (components/runtime/TailwindCdnRuntime.tsx,
// also used inside srcdoc preview iframes, which inherit this policy), Google
// Fonts stylesheets referenced by deck themes, Mixpanel (utils/mixpanel.ts),
// and https: images because deck images come from user-configured providers
// (see images.remotePatterns). Next.js emits inline scripts/styles without a
// nonce, and dev/HMR needs eval, so those stay allowed while report-only.
export const appPageContentSecurityPolicyReportOnly = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com",
  "img-src 'self' data: blob: https:",
  "media-src 'self' data: blob:",
  "connect-src 'self' https://api-eu.mixpanel.com https://fonts.googleapis.com https://fonts.gstatic.com",
  "worker-src 'self' blob:",
  "frame-src 'self' blob: data:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'self'",
].join("; ");

// Renderer/export surfaces that already emit their own per-response CSP
// (FastAPI share assets, workspace artifacts, the /pdf-maker export view) and
// the proxied backend/static paths are left out so nothing is double-governed.
export const appPageCspSource =
  "/((?!pdf-maker|p/|api/|_next/|static/|app_data/|favicon\\.ico|.*\\.(?:png|svg|ico|txt|xml|webmanifest)$).*)";

const nextConfig = {
  reactStrictMode: false,
  distDir: process.env.PRESENTON_TEST_NEXT_DIST_DIR || ".next-build",
  // Next requires this to agree with outputFileTracingRoot. The repository root
  // is required to trace the presentation-export runtime outside this app.
  turbopack: {
    root: repositoryRoot,
  },
  experimental: {
    // Fidelity tests isolate their distDir per run. Avoid duplicating Turbopack's
    // persistent dev cache on disk in that already-disposable Windows workspace.
    turbopackFileSystemCacheForDev: !process.env.PRESENTON_TEST_NEXT_DIST_DIR,
    // Native authored-template envelopes include digest-pinned OOXML fragments
    // and element assets. Keep Next's middleware clone limit aligned with the
    // internal worker route so valid requests are never silently truncated.
    proxyClientMaxBodySize: "16mb",
  },
  output: "standalone",
  // Runtime template CSS compilation needs Tailwind's real package __dirname.
  // Bundling it can bake the build machine path into fs.readFileSync calls.
  serverExternalPackages: ["postcss", "tailwindcss"],
  outputFileTracingRoot: repositoryRoot,
  outputFileTracingIncludes: {
    "/*": [
      ...semverRuntimeTrace,
      ...sharpNativeRuntimeTrace,
      ...linkFreeRuntimeTrace,
      ...runtimeTailwindTrace,
    ],
    "/api/export-presentation": exportRuntimeTrace,
  },
  outputFileTracingExcludes: {
    "/*": [
      "../../.git/**/*",
      "../../.cache/**/*",
      "../../electron/dist/resources/**/*",
      "../../electron/src/**/*",
      "../../docs/**/*",
      "../../servers/fastapi/**/*",
      "../../**/.next*/cache/**/*",
      "../../**/__pycache__/**/*",
      "../../**/tests/**/*",
      "../../**/cypress/**/*",
      "../../**/*.test.*",
      "../../**/*.spec.*",
      "../../**/*.cy.*",
      "../../**/package-lock.json",
      "../../**/pnpm-lock.yaml",
      "../../**/yarn.lock",
      "../../**/uv.lock",
      "../../**/*.tsbuildinfo",
    ],
  },
  ...(process.env.NODE_ENV !== "production"
    ? {
        allowedDevOrigins: [
          "http://127.0.0.1:40001",
          "http://localhost:40001",
          "127.0.0.1",
          "localhost",
        ],
      }
    : {}),

  // Keep direct Next.js access usable in development as well as the
  // single-origin local proxy. Browser API calls are same-origin, so without
  // this rewrite opening port 3000 directly returns a Next.js 404.
  async rewrites() {
    const fastApiUrl =
      process.env.FAST_API_INTERNAL_URL || "http://127.0.0.1:8000";

    return [
      {
        source: "/api/v1/:path*",
        destination: `${fastApiUrl}/api/v1/:path*`,
      },
      {
        source: "/app_data/:path*",
        destination: `${fastApiUrl}/app_data/:path*`,
      },
    ];
  },

  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
      {
        source: appPageCspSource,
        headers: [
          {
            key: "Content-Security-Policy-Report-Only",
            value: appPageContentSecurityPolicyReportOnly,
          },
        ],
      },
    ];
  },

  // /compose is a friendly alias for the main compose screen (/upload).
  async redirects() {
    return [
      {
        source: '/compose',
        destination: '/upload',
        permanent: false,
      },
    ];
  },

  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "pub-7c765f3726084c52bcd5d180d51f1255.r2.dev",
      },
      {
        protocol: "https",
        hostname: "pptgen-public.ap-south-1.amazonaws.com",
      },
      {
        protocol: "https",
        hostname: "pptgen-public.s3.ap-south-1.amazonaws.com",
      },
      {
        protocol: "https",
        hostname: "img.icons8.com",
      },
      {
        protocol: "https",
        hostname: "present-for-me.s3.amazonaws.com",
      },
      {
        protocol: "https",
        hostname: "yefhrkuqbjcblofdcpnr.supabase.co",
      },
      {
        protocol: "https",
        hostname: "images.unsplash.com",
      },
      {
        protocol: "https",
        hostname: "picsum.photos",
      },
      {
        protocol: "https",
        hostname: "unsplash.com",
      },
    ],
  },
  
};

export default nextConfig;
