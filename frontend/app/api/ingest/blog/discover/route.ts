import { type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/**
 * Kicks off an ingest run: discover candidate posts for a blog URL, always
 * landing on `awaiting_confirmation` — no auto-start, even for a small blog.
 * Thin proxy to the Python backend's `/api/ingest` router (its own
 * subsystem, separate from the chat agent routes).
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (payload: unknown, status: number) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

export async function POST(req: NextRequest) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return json({ error: "unauthenticated" }, 401);
  }

  let body: { url?: string };
  try {
    body = (await req.json()) as { url?: string };
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  if (!body.url) {
    return json({ error: "url is required" }, 400);
  }

  const upstream = await fetch(`${backendUrl()}/api/ingest/blog/discover`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-signal-proxy-secret": proxySecret(),
    },
    body: JSON.stringify({ url: body.url, user_id: user?.sub ?? null }),
  });

  return json(await upstream.json(), upstream.status);
}
