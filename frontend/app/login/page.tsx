import Link from "next/link";
import { redirect } from "next/navigation";

const AUTH_ERRORS: Record<string, string> = {
  bad_state: "That sign-in link expired or didn't match. Please try again.",
  missing_code: "Google didn't return a sign-in code. Please try again.",
  exchange_failed: "We couldn't finish signing in with Google. Please try again.",
  access_denied: "Sign-in was cancelled.",
};

export const dynamic = "force-dynamic";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ returnTo?: string; auth_error?: string }>;
}) {
  const sp = await searchParams;

  if ((process.env.GOOGLE_AUTH_ENABLED ?? "").toLowerCase() !== "true") {
    redirect("/app");
  }

  const returnTo =
    sp.returnTo && sp.returnTo.startsWith("/") ? sp.returnTo : "/app";
  const loginHref = `/api/auth/login?returnTo=${encodeURIComponent(returnTo)}`;
  const errorMessage = sp.auth_error
    ? (AUTH_ERRORS[sp.auth_error] ?? "Sign-in didn't complete. Please try again.")
    : null;

  return (
    <main className="auth-gate">
      <div className="auth-gate-card">
        <div className="auth-gate-mark">S</div>
        <h1>Sign in to Scratchpad</h1>
        <p>Your threads and scratchpad live with your Google account.</p>
        {errorMessage ? (
          <p className="auth-gate-error">{errorMessage}</p>
        ) : null}
        <a className="auth-gate-btn" href={loginHref}>
          Continue with Google
        </a>
        <Link className="auth-gate-back" href="/">
          Back to home
        </Link>
      </div>
    </main>
  );
}
