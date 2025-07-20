import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);


/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  
  // Enable experimental features for better HMR
  experimental: {
    optimizePackageImports: ['presentation-layouts'],
  },

  // Webpack configuration for better hot reloading
  webpack: (config, { dev, isServer }) => {
    if (dev && !isServer) {
      // Enable hot reloading for presentation layouts
      config.watchOptions = {
        ...config.watchOptions,
        ignored: /node_modules/,
        poll: 1000, 
        aggregateTimeout: 300, 
      };

      // Add alias for presentation layouts
      config.resolve.alias = {
        ...config.resolve.alias,
        '@/presentation-layouts': path.join(process.cwd(), 'presentation-layouts'),
      };

      // Configure module resolution for better hot reloading
      config.resolve.symlinks = false;
      config.resolve.cache = false;

      // Optimize for faster rebuilds
      config.cache = {
        type: 'filesystem',
        buildDependencies: {
          config: [__filename],
        },
        cacheDirectory: path.join(process.cwd(), '.next/cache/webpack'),
      };
    }

    return config;
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
