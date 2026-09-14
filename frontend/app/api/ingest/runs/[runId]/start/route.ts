import { type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/**
 * Confirms the selection and starts scraping. Required in every case, even
 * to accept the pre-checked defaults as-is — see docs/plan-house-voice-reader.md.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (payload: unknown, status: number) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

export async function POST(
  req: NextRequest,
  ctx: RouteContext<"/api/ingest/runs/[runId]/start">,
) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return json({ error: "unauthenticated" }, 401);
  }
  const { runId } = await ctx.params;

  const upstream = await fetch(
    `${backendUrl()}/api/ingest/runs/${runId}/start`,
    {
      method: "POST",
      headers: { "x-signal-proxy-secret": proxySecret() },
    },
  );
  return json(await upstream.json(), upstream.status);
}
