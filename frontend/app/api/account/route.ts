import { NextResponse, type NextRequest } from "next/server";

import { authEnabled, backendUrl, currentUser, proxySecret } from "@/lib/auth";

/**
 * Credit balance for the signed-in user. Thin proxy to the Python backend's
 * `/api/account`, injecting the verified identity + shared secret.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const OFF = { credits_enabled: false, credits_balance: null, status: "active" };

export async function GET(req: NextRequest) {
  const user = currentUser(req);
  if (authEnabled() && !user) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  if (!user) return NextResponse.json(OFF);

  try {
    const upstream = await fetch(
      `${backendUrl()}/api/account?user_id=${encodeURIComponent(user.sub)}`,
      { headers: { "x-signal-proxy-secret": proxySecret() }, cache: "no-store" },
    );
    if (!upstream.ok) return NextResponse.json(OFF);
    return NextResponse.json(await upstream.json());
  } catch {
    return NextResponse.json(OFF);
  }
}
