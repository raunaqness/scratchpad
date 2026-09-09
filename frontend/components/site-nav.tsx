"use client";

import { Moon, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const THEME_KEY = "signal-theme";

function getInitialTheme(): "light" | "dark" {
  if (typeof window === "undefined") return "light";
  const saved = window.localStorage.getItem(THEME_KEY);
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

type MeState =
  | { status: "loading" }
  | { status: "anon" }
  | { status: "authed"; name: string; disabled: boolean };

function useMe(): MeState {
  const [state, setState] = useState<MeState>({ status: "loading" });
  useEffect(() => {
    let alive = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!alive) return;
        const user = data?.user;
        if (user?.sub) {
          setState({
            status: "authed",
            name: user.name || user.email || "Account",
            disabled: Boolean(data.auth_disabled),
          });
        } else {
          setState({ status: "anon" });
        }
      })
      .catch(() => alive && setState({ status: "anon" }));
    return () => {
      alive = false;
    };
  }, []);
  return state;
}

function AuthNav() {
  const me = useMe();
  if (me.status === "loading") return null;
  if (me.status === "authed") {
    if (me.disabled) return null; // local dev — nothing to sign out of
    return (
      <span className="site-nav-user">
        <span className="site-nav-user-name">{me.name}</span>
        <a className="site-nav-link" href="/api/auth/logout">
          Sign out
        </a>
      </span>
    );
  }
  return (
    <a className="site-nav-link" href="/api/auth/login">
      Sign in
    </a>
  );
}

export function SiteNav() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const pathname = usePathname();
  const inApp = pathname?.startsWith("/app") ?? false;

  useEffect(() => {
    const initialTheme = getInitialTheme();
    setTheme(initialTheme);
    document.documentElement.classList.toggle("dark", initialTheme === "dark");
  }, []);

  function toggleTheme() {
    const nextTheme = theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
    document.documentElement.classList.toggle("dark", nextTheme === "dark");
    window.localStorage.setItem(THEME_KEY, nextTheme);
  }

  return (
    <nav className="site-nav">
      <Link href="/" className="site-brand" aria-label="Scratchpad home">
        <span className="site-brand-mark">S</span>
        <span>Scratchpad</span>
      </Link>
      <div className="site-nav-actions">
        <Link href="/about" className="site-nav-link">
          About
        </Link>
        {!inApp ? <AuthNav /> : null}
        <button
          type="button"
          className="theme-toggle"
          onClick={toggleTheme}
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
        >
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </button>
      </div>
    </nav>
  );
}
