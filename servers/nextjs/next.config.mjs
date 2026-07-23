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

const nextConfig = {
  reactStrictMode: false,
  distDir: ".next-build",
  output: "standalone",
  outputFileTracingRoot: repositoryRoot,
  outputFileTracingIncludes: {
    "/*": [
      ...semverRuntimeTrace,
      ...sharpNativeRuntimeTrace,
      ...linkFreeRuntimeTrace,
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

  // Rewrites for development - proxy font requests to FastAPI backend
  async rewrites() {
    return [
      {
        source: '/app_data/fonts/:path*',
        destination: 'http://localhost:5000/app_data/fonts/:path*',
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
