import { type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/** Run status + per-item stages — what the ingest page polls for progress. */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (payload: unknown, status: number) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

export async function GET(
  req: NextRequest,
  ctx: RouteContext<"/api/ingest/runs/[runId]">,
) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return json({ error: "unauthenticated" }, 401);
  }
  const { runId } = await ctx.params;

  const upstream = await fetch(`${backendUrl()}/api/ingest/runs/${runId}`, {
    headers: { "x-signal-proxy-secret": proxySecret() },
    cache: "no-store",
  });
  return json(await upstream.json(), upstream.status);
}
