"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export type AuthUser = {
  sub: string;
  email?: string;
  name?: string;
  picture?: string;
};

export type AuthState =
  | { status: "loading" }
  | { status: "authed"; user: AuthUser; authDisabled: boolean }
  | { status: "anon" };

const AuthContext = createContext<AuthState>({ status: "loading" });

export const useAuth = (): AuthState => useContext(AuthContext);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    let alive = true;
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!alive) return;
        if (data?.user?.sub) {
          setState({
            status: "authed",
            user: data.user as AuthUser,
            authDisabled: Boolean(data.auth_disabled),
          });
        } else {
          setState({ status: "anon" });
        }
      })
      .catch(() => {
        if (alive) setState({ status: "anon" });
      });
    return () => {
      alive = false;
    };
  }, []);

  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>;
}
