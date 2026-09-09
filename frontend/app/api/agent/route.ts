import { type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/**
 * SSE proxy to the Python agent. Reads the session cookie, injects the verified
 * identity into the AG-UI request body, and streams the backend's
 * `text/event-stream` straight back to the browser.
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

  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const identity = {
    user_id: user?.sub,
    user_email: user?.email ?? null,
    user_name: user?.name ?? null,
  };
  const merge = (key: "forwardedProps" | "forwarded_props") => {
    const existing =
      body[key] && typeof body[key] === "object"
        ? (body[key] as Record<string, unknown>)
        : {};
    body[key] = { ...existing, ...identity };
  };
  merge("forwardedProps");
  merge("forwarded_props");

  const upstream = await fetch(`${backendUrl()}/agent`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      "x-signal-proxy-secret": proxySecret(),
    },
    body: JSON.stringify(body),
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type":
        upstream.headers.get("content-type") ?? "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
