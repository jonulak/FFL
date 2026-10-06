import type { NextConfig } from "next";

const API = process.env.FFL_API_URL ?? "http://127.0.0.1:8300";

const nextConfig: NextConfig = {
  // `next dev` only hydrates pages served from localhost unless the host is listed here; a
  // worktree test server is reached over loopback and the tailnet (see CLAUDE.md, test servers).
  allowedDevOrigins: ["127.0.0.1", "macbook-pro.tailnet.ts.net"],
  // the page was Waivers until week 5: old bookmarks and home-screen links land on Players, query kept
  async redirects() {
    return [{ source: "/waivers", destination: "/players", permanent: true }];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
};

export default nextConfig;
