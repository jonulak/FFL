import type { NextConfig } from "next";

const API = process.env.FFL_API_URL ?? "http://127.0.0.1:8300";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/:path*` }];
  },
};

export default nextConfig;
