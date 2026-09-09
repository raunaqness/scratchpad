import crypto from "node:crypto";

/**
 * Minimal HS256 compact-JWT sign/verify for the session cookie. Kept
 * dependency-free (Node `crypto`) — the tokens we mint never leave this app,
 * and Google's `id_token` is trusted by transport, not re-verified here.
 */

const encode = (value: string): string =>
  Buffer.from(value, "utf8").toString("base64url");

const decode = (value: string): string =>
  Buffer.from(value, "base64url").toString("utf8");

export function signJwt(
  payload: Record<string, unknown>,
  secret: string,
  ttlSeconds: number,
): string {
  const now = Math.floor(Date.now() / 1000);
  const head = encode(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const body = encode(
    JSON.stringify({ iat: now, exp: now + ttlSeconds, ...payload }),
  );
  const data = `${head}.${body}`;
  const sig = crypto
    .createHmac("sha256", secret)
    .update(data)
    .digest("base64url");
  return `${data}.${sig}`;
}

export function verifyJwt<T = Record<string, unknown>>(
  token: string,
  secret: string,
): T | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [head, body, sig] = parts;
  const expected = crypto
    .createHmac("sha256", secret)
    .update(`${head}.${body}`)
    .digest("base64url");
  const a = Buffer.from(sig);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  try {
    const claims = JSON.parse(decode(body)) as Record<string, unknown>;
    if (
      typeof claims.exp === "number" &&
      claims.exp < Math.floor(Date.now() / 1000)
    ) {
      return null;
    }
    return claims as T;
  } catch {
    return null;
  }
}

/** Decode a JWT payload without checking the signature (Google id_token). */
export function decodeJwtPayload<T = Record<string, unknown>>(
  token: string,
): T | null {
  const parts = token.split(".");
  if (parts.length < 2) return null;
  try {
    return JSON.parse(decode(parts[1])) as T;
  } catch {
    return null;
  }
}
