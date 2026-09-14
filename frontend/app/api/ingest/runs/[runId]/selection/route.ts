import { type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/** User's edits to the candidate checklist. Backend enforces the 15-item cap. */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (payload: unknown, status: number) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

export async function PATCH(
  req: NextRequest,
  ctx: RouteContext<"/api/ingest/runs/[runId]/selection">,
) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return json({ error: "unauthenticated" }, 401);
  }
  const { runId } = await ctx.params;

  let body: { selected_urls?: string[] };
  try {
    body = (await req.json()) as { selected_urls?: string[] };
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const upstream = await fetch(
    `${backendUrl()}/api/ingest/runs/${runId}/selection`,
    {
      method: "PATCH",
      headers: {
        "content-type": "application/json",
        "x-signal-proxy-secret": proxySecret(),
      },
      body: JSON.stringify({ selected_urls: body.selected_urls ?? [] }),
    },
  );
  return json(await upstream.json(), upstream.status);
}
