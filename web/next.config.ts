import type { NextConfig } from "next";

/**
 * The site ships as a static export so it can be served from GitHub Pages under a
 * repository subpath. `NEXT_PUBLIC_BASE_PATH` carries that subpath (for example
 * `/trajectory`) and is empty for a root deployment or local development.
 */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const nextConfig: NextConfig = {
  output: "export",
  basePath,
  // assetPrefix must stay undefined rather than "" so the dev server keeps working.
  assetPrefix: basePath || undefined,
  // Directory style URLs (`/runs/<id>/index.html`) resolve on every static file server,
  // including GitHub Pages and the `serve` used by the Playwright suite.
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
};

export default nextConfig;
