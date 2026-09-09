import { NextResponse, type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/** List a thread's generated artifacts. Thin proxy to the Python backend. */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const threadId = req.nextUrl.searchParams.get("thread_id") ?? "";
  if (!threadId) {
    return NextResponse.json({ artifacts: [] });
  }
  try {
    const upstream = await fetch(
      `${backendUrl()}/api/artifacts?thread_id=${encodeURIComponent(threadId)}`,
      { headers: { "x-signal-proxy-secret": proxySecret() }, cache: "no-store" },
    );
    if (!upstream.ok) return NextResponse.json({ artifacts: [] });
    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json({ artifacts: [] });
  }
}
