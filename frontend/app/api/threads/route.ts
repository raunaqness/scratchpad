import { NextResponse, type NextRequest } from "next/server";

import { backendUrl, currentUser, proxySecret } from "@/lib/auth";

/** Thin proxy to the backend thread registry, scoped to the signed-in user. */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const user = currentUser(req);
  if (!user) return NextResponse.json({ threads: [] }, { status: 401 });

  try {
    const res = await fetch(
      `${backendUrl()}/api/threads?user_id=${encodeURIComponent(user.sub)}`,
      { headers: { "x-signal-proxy-secret": proxySecret() }, cache: "no-store" },
    );
    if (!res.ok) return NextResponse.json({ threads: [] });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ threads: [] });
  }
}

export async function POST(req: NextRequest) {
  const user = currentUser(req);
  if (!user) return NextResponse.json({ error: "unauthenticated" }, { status: 401 });

  const body = (await req.json().catch(() => ({}))) as {
    thread_id?: string;
    title?: string;
  };
  if (!body.thread_id) {
    return NextResponse.json({ error: "thread_id required" }, { status: 400 });
  }

  try {
    const res = await fetch(`${backendUrl()}/api/threads`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-signal-proxy-secret": proxySecret(),
      },
      body: JSON.stringify({
        user_id: user.sub,
        thread_id: body.thread_id,
        title: body.title ?? "",
      }),
    });
    return NextResponse.json(await res.json().catch(() => ({})), {
      status: res.status,
    });
  } catch {
    return NextResponse.json({ error: "backend_unreachable" }, { status: 502 });
  }
}
