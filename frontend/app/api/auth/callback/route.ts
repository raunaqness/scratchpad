import { NextResponse, type NextRequest } from "next/server";

import {
  OAUTH_STATE_COOKIE,
  SESSION_COOKIE,
  SESSION_TTL_SECONDS,
  appOrigin,
  authEnabled,
  cookieSecure,
  sessionSecret,
} from "@/lib/auth";
import { exchangeCode } from "@/lib/google";
import { signJwt, verifyJwt } from "@/lib/jwt";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const origin = appOrigin(req);
  const fail = (reason: string) => {
    const dest = new URL("/login", origin);
    dest.searchParams.set("auth_error", reason);
    return NextResponse.redirect(dest);
  };

  if (!authEnabled()) {
    return NextResponse.redirect(new URL("/app", origin));
  }

  const params = req.nextUrl.searchParams;
  if (params.get("error")) return fail(params.get("error") as string);

  const code = params.get("code");
  const state = params.get("state");
  if (!code || !state) return fail("missing_code");

  const stateClaims = verifyJwt<{ n: string; r?: string }>(
    state,
    sessionSecret(),
  );
  const nonce = req.cookies.get(OAUTH_STATE_COOKIE)?.value;
  if (!stateClaims || !nonce || stateClaims.n !== nonce) {
    return fail("bad_state");
  }

  let profile;
  try {
    profile = await exchangeCode(code);
  } catch {
    return fail("exchange_failed");
  }

  const token = signJwt(
    {
      sub: profile.sub,
      email: profile.email,
      name: profile.name,
      picture: profile.picture,
    },
    sessionSecret(),
    SESSION_TTL_SECONDS,
  );

  const returnTo =
    stateClaims.r && stateClaims.r.startsWith("/") ? stateClaims.r : "/app";
  const res = NextResponse.redirect(new URL(returnTo, origin));
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: cookieSecure(),
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  });
  res.cookies.set(OAUTH_STATE_COOKIE, "", { path: "/", maxAge: 0 });
  return res;
}
