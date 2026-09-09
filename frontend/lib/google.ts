import { decodeJwtPayload } from "@/lib/jwt";

/** Google OAuth 2.0 (OpenID Connect) authorization-code flow. */

const AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token";
const VALID_ISSUERS = ["https://accounts.google.com", "accounts.google.com"];

export function googleConfig() {
  const clientId = process.env.GOOGLE_CLIENT_ID;
  const clientSecret = process.env.GOOGLE_CLIENT_SECRET;
  const redirectUri = process.env.GOOGLE_REDIRECT_URI;
  if (!clientId || !clientSecret || !redirectUri) {
    throw new Error(
      "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI must be set",
    );
  }
  return { clientId, clientSecret, redirectUri };
}

export function buildAuthUrl(state: string): string {
  const { clientId, redirectUri } = googleConfig();
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: "openid email profile",
    state,
    access_type: "online",
    prompt: "select_account",
  });
  return `${AUTH_ENDPOINT}?${params.toString()}`;
}

export type GoogleProfile = {
  sub: string;
  email?: string;
  email_verified?: boolean;
  name?: string;
  picture?: string;
};

type IdTokenClaims = GoogleProfile & {
  aud?: string;
  iss?: string;
  exp?: number;
};

export async function exchangeCode(code: string): Promise<GoogleProfile> {
  const { clientId, clientSecret, redirectUri } = googleConfig();

  const res = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: redirectUri,
      grant_type: "authorization_code",
    }),
  });
  if (!res.ok) {
    throw new Error(
      `google token exchange failed: ${res.status} ${await res.text()}`,
    );
  }

  const token = (await res.json()) as { id_token?: string };
  if (!token.id_token) throw new Error("google token response had no id_token");

  const claims = decodeJwtPayload<IdTokenClaims>(token.id_token);
  if (!claims?.sub) throw new Error("id_token had no sub");

  // The id_token came directly from Google's TLS token endpoint in a
  // server-to-server call, so per OIDC Core 3.1.3.7 we may skip signature
  // verification. Still sanity-check audience, issuer and freshness.
  if (claims.aud && claims.aud !== clientId) {
    throw new Error("id_token audience mismatch");
  }
  if (claims.iss && !VALID_ISSUERS.includes(claims.iss)) {
    throw new Error("id_token issuer mismatch");
  }
  if (typeof claims.exp === "number" && claims.exp * 1000 < Date.now()) {
    throw new Error("id_token expired");
  }

  return {
    sub: claims.sub,
    email: claims.email,
    email_verified: claims.email_verified,
    name: claims.name,
    picture: claims.picture,
  };
}
