import { NextResponse, type NextRequest } from "next/server";

/**
 * Server-side gate for the app. Runs before `/app` renders: if Google auth is
 * on and there's no session cookie, bounce to `/login` (no client-side flash).
 * Cookie *validity* is checked in the Node route handlers (`/api/auth/me`,
 * `/api/agent`) — this only checks presence, since the proxy runtime has no
 * `node:crypto`.
 */

const SESSION_COOKIE = "scratchpad_session";

export function proxy(req: NextRequest) {
  if ((process.env.GOOGLE_AUTH_ENABLED ?? "").toLowerCase() !== "true") {
    return NextResponse.next();
  }
  if (req.cookies.get(SESSION_COOKIE)?.value) {
    return NextResponse.next();
  }
  const login = new URL("/login", req.nextUrl);
  login.searchParams.set("returnTo", req.nextUrl.pathname + req.nextUrl.search);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/app/:path*"],
};
