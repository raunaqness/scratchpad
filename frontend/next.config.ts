import { withAui } from "@assistant-ui/next";
import type { NextConfig } from "next";

// The agent is reached through the `/api/agent` Route Handler (see
// app/api/agent/route.ts), which injects the authenticated user and stream-
// proxies to the Python backend. No rewrite needed.
const nextConfig: NextConfig = {
  // The dev deployment (docker-compose.test.yml) runs `next dev` behind the
  // dev-scratchpad.raunaqness.com tunnel, not localhost — without this,
  // Next.js blocks HMR/dev-resource requests from that origin as untrusted
  // cross-origin traffic, which is what a "stuck" /app was actually caused
  // by (confirmed via container logs: "Blocked cross-origin request to
  // Next.js dev resource /_next/hmr"). Harmless for the production image,
  // which runs `next start` and never consults this option.
  allowedDevOrigins: ["dev-scratchpad.raunaqness.com"],
};

export default withAui(nextConfig);
