import { withAui } from "@assistant-ui/next";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/agent/:path*",
        destination: `${process.env.SIGNAL_BACKEND_URL ?? "http://backend:8001"}/agent/:path*`,
      },
    ];
  },
};

export default withAui(nextConfig);
