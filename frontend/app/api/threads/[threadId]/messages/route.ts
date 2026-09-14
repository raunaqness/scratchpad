import { type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/** A thread's chat history + last scratchpad snapshot — what the sidebar
 * fetches when the user clicks an older thread, to resume it. */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (payload: unknown, status: number) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

export async function GET(
  req: NextRequest,
  ctx: RouteContext<"/api/threads/[threadId]/messages">,
) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return json({ error: "unauthenticated" }, 401);
  }
  if (!user?.sub) {
    return json({ error: "unauthenticated" }, 401);
  }
  const { threadId } = await ctx.params;

  const upstream = await fetch(
    `${backendUrl()}/api/threads/${threadId}/messages?user_id=${encodeURIComponent(user.sub)}`,
    {
      headers: { "x-signal-proxy-secret": proxySecret() },
      cache: "no-store",
    },
  );
  return json(await upstream.json().catch(() => ({})), upstream.status);
}
