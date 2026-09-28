import path from "node:path";
import type { NextConfig } from "next";

// The browser only ever talks to this app. /api and /media are proxied to the
// FastAPI backend, so its HTTP-only auth cookies are first-party in every environment.
const API_URL = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  // Self-contained server for the Docker image (set there); Vercel manages its own output.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  // The monorepo has other lockfiles above this folder; pin the root so Turbopack doesn't guess.
  turbopack: { root: path.resolve(__dirname) },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_URL}/api/:path*` },
      { source: "/media/:path*", destination: `${API_URL}/media/:path*` },
    ];
  },
};

export default nextConfig;
