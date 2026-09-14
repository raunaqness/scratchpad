import { type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/** Ask a question against the account's whole knowledge graph — every
 * ingested blog at once, not one run at a time. */

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
  if (!user?.sub) {
    return json({ error: "unauthenticated" }, 401);
  }

  let body: { question?: string };
  try {
    body = (await req.json()) as { question?: string };
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  if (!body.question) {
    return json({ error: "question is required" }, 400);
  }

  const upstream = await fetch(`${backendUrl()}/api/ingest/query`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-signal-proxy-secret": proxySecret(),
    },
    body: JSON.stringify({ question: body.question, user_id: user.sub }),
  });
  return json(await upstream.json(), upstream.status);
}
