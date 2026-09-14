import { type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/**
 * The account's whole ingest library in one call: ingested articles, slots
 * remaining under the 15-article cap, and any run still in progress. This
 * is what the ingest page loads on every visit — the knowledge graph is
 * account-wide, not tied to a specific run/URL.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (payload: unknown, status: number) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

export async function GET(req: NextRequest) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return json({ error: "unauthenticated" }, 401);
  }
  if (!user?.sub) {
    return json({ count: 0, max: 15, remaining: 15, items: [], active_run: null }, 200);
  }

  const upstream = await fetch(
    `${backendUrl()}/api/ingest/library?user_id=${encodeURIComponent(user.sub)}`,
    {
      headers: { "x-signal-proxy-secret": proxySecret() },
      cache: "no-store",
    },
  );
  return json(await upstream.json(), upstream.status);
}
