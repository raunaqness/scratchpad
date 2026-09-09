import crypto from "node:crypto";

import { NextResponse, type NextRequest } from "next/server";

import {
  OAUTH_STATE_COOKIE,
  OAUTH_STATE_TTL_SECONDS,
  appOrigin,
  authEnabled,
  cookieSecure,
  sessionSecret,
} from "@/lib/auth";
import { buildAuthUrl } from "@/lib/google";
import { signJwt } from "@/lib/jwt";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (!authEnabled()) {
    return NextResponse.redirect(new URL("/app", appOrigin(req)));
  }

  const returnToParam = req.nextUrl.searchParams.get("returnTo");
  const returnTo =
    returnToParam && returnToParam.startsWith("/") ? returnToParam : "/app";

  const nonce = crypto.randomUUID();
  const state = signJwt(
    { n: nonce, r: returnTo },
    sessionSecret(),
    OAUTH_STATE_TTL_SECONDS,
  );

  const res = NextResponse.redirect(buildAuthUrl(state));
  res.cookies.set(OAUTH_STATE_COOKIE, nonce, {
    httpOnly: true,
    secure: cookieSecure(),
    sameSite: "lax",
    path: "/",
    maxAge: OAUTH_STATE_TTL_SECONDS,
  });
  return res;
}
