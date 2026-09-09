import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE, appOrigin, cookieSecure } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function clearSession(req: NextRequest) {
  const res = NextResponse.redirect(new URL("/", appOrigin(req)));
  res.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true,
    secure: cookieSecure(),
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return res;
}

export async function GET(req: NextRequest) {
  return clearSession(req);
}

export async function POST(req: NextRequest) {
  return clearSession(req);
}
