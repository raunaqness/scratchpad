import { NextResponse, type NextRequest } from "next/server";

import { DEV_USER, authEnabled, readSession } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (!authEnabled()) {
    return NextResponse.json({ user: { ...DEV_USER }, auth_disabled: true });
  }
  const user = readSession(req);
  if (!user) {
    return NextResponse.json({ user: null }, { status: 401 });
  }
  return NextResponse.json({ user });
}
