import { withAui } from "@assistant-ui/next";
import type { NextConfig } from "next";

// The agent is reached through the `/api/agent` Route Handler (see
// app/api/agent/route.ts), which injects the authenticated user and stream-
// proxies to the Python backend. No rewrite needed.
const nextConfig: NextConfig = {};

export default withAui(nextConfig);
