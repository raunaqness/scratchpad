import { type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/** Removes one ingested article — from the graph, the stored file, and the DB row. */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (payload: unknown, status: number) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

export async function DELETE(
  req: NextRequest,
  ctx: RouteContext<"/api/ingest/items/[itemId]">,
) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return json({ error: "unauthenticated" }, 401);
  }
  if (!user?.sub) {
    return json({ error: "unauthenticated" }, 401);
  }
  const { itemId } = await ctx.params;

  const upstream = await fetch(
    `${backendUrl()}/api/ingest/items/${itemId}?user_id=${encodeURIComponent(user.sub)}`,
    {
      method: "DELETE",
      headers: { "x-signal-proxy-secret": proxySecret() },
    },
  );
  return json(await upstream.json(), upstream.status);
}
