import type { NextRequest } from "next/server";

import { verifyJwt } from "@/lib/jwt";

/**
 * Server-side auth helpers for the Next.js BFF. Google OAuth lives entirely
 * here; the Python backend only trusts the `user_id` this app injects, gated by
 * `SIGNAL_SESSION_SECRET` shared as `x-signal-proxy-secret`.
 */

export const SESSION_COOKIE = "scratchpad_session";
export const OAUTH_STATE_COOKIE = "scratchpad_oauth_state";
export const SESSION_TTL_SECONDS = 60 * 60 * 24 * 7; // 7 days
export const OAUTH_STATE_TTL_SECONDS = 600;

export type SessionUser = {
  sub: string;
  email?: string;
  name?: string;
  picture?: string;
};

export const DEV_USER: SessionUser = {
  sub: "dev-user",
  email: "dev@localhost",
  name: "Local Dev",
};

export function authEnabled(): boolean {
  return (process.env.GOOGLE_AUTH_ENABLED ?? "").toLowerCase() === "true";
}

export function cookieSecure(): boolean {
  return (process.env.SIGNAL_COOKIE_SECURE ?? "true").toLowerCase() !== "false";
}

export function sessionSecret(): string {
  const secret = process.env.SIGNAL_SESSION_SECRET;
  if (!secret) throw new Error("SIGNAL_SESSION_SECRET is not set");
  return secret;
}

/** The shared secret the backend checks on `/agent` and `/api/*`. */
export function proxySecret(): string {
  return process.env.SIGNAL_SESSION_SECRET ?? "";
}

export function backendUrl(): string {
  return process.env.SIGNAL_BACKEND_URL ?? "http://backend:8001";
}

/**
 * The public origin of this deployment, for building redirect targets.
 *
 * Behind the Cloudflare tunnel `req.url` is the internal `http://localhost:5173`,
 * so `new URL("/app", req.url)` would send the browser there. Prefer the
 * forwarded host, then the origin of `GOOGLE_REDIRECT_URI` (which IS the public
 * origin, per deployment), then whatever the request claims.
 */
export function appOrigin(req: NextRequest): string {
  const forwardedHost = req.headers.get("x-forwarded-host");
  if (forwardedHost) {
    const proto = req.headers.get("x-forwarded-proto") ?? "https";
    return `${proto}://${forwardedHost}`;
  }
  const redirectUri = process.env.GOOGLE_REDIRECT_URI;
  if (redirectUri) {
    try {
      return new URL(redirectUri).origin;
    } catch {
      /* malformed env — fall through */
    }
  }
  return req.nextUrl.origin;
}

export function readSession(req: NextRequest): SessionUser | null {
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  const claims = verifyJwt<SessionUser>(token, sessionSecret());
  if (!claims?.sub) return null;
  return {
    sub: claims.sub,
    email: claims.email,
    name: claims.name,
    picture: claims.picture,
  };
}

/** The effective user for a request: real session, or the dev user when auth is off. */
export function currentUser(req: NextRequest): SessionUser | null {
  return authEnabled() ? readSession(req) : { ...DEV_USER };
}
