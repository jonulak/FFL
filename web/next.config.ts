import type { NextConfig } from "next";

const API = process.env.FFL_API_URL ?? "http://127.0.0.1:8300";

const nextConfig: NextConfig = {
  // `next dev` only hydrates pages served from localhost unless the host is listed here; a
  // worktree test server is reached over loopback and the tailnet (see CLAUDE.md, test servers).
  allowedDevOrigins: ["127.0.0.1", "macbook-pro.tailnet.ts.net"],
  // Next cuts a rewrite's upstream call at 30 s by default; a claims submit is one ESPN call per cancel,
  // add and claim in turn, and the dialog waits up to lib/claims.ts's SUBMIT_CEILING_MS for it
  experimental: { proxyTimeout: 300_000 },
  // the page was Waivers until week 5: old bookmarks and home-screen links land on Players, query kept
  async redirects() {
    return [{ source: "/waivers", destination: "/players", permanent: true }];
  },
  // the service worker must never be served from a cache, or a fix to it never reaches the phone
  async headers() {
    return [{ source: "/sw.js", headers: [
      { key: "Cache-Control", value: "no-cache, no-store, must-revalidate" },
      { key: "Content-Type", value: "application/javascript; charset=utf-8" },
    ] }];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
};

export default nextConfig;
