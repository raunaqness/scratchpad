"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  AssistantRuntimeProvider,
  type ThreadMessage,
} from "@assistant-ui/react";
import { HttpAgent } from "@ag-ui/client";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";

import { AuthProvider, useAuth, type AuthUser } from "@/app/auth-context";

const ThreadIdContext = createContext<string | null>(null);

/** The active thread id, for callers off the AG-UI stream (e.g. skill runs). */
export function useThreadId(): string {
  const id = useContext(ThreadIdContext);
  if (!id) throw new Error("useThreadId outside MyRuntimeProvider");
  return id;
}

type StoredThread = {
  id: string;
  messages: readonly ThreadMessage[];
};

/**
 * AG-UI runtime, gated on a Google session. Threads are keyed per user and
 * persisted in `localStorage`, so a refresh resumes the same conversation and
 * "New Thread" is the only thing that starts a fresh one. The backend registers
 * each thread on its first turn.
 */
export function MyRuntimeProvider({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <AuthProvider>
      <RuntimeGate>{children}</RuntimeGate>
    </AuthProvider>
  );
}

function RuntimeGate({ children }: { children: ReactNode }) {
  const auth = useAuth();

  // The proxy gate catches the no-cookie case server-side; this only fires for
  // a cookie that turned out to be invalid or expired.
  useEffect(() => {
    if (auth.status === "anon") {
      window.location.href = "/login?returnTo=/app";
    }
  }, [auth.status]);

  if (auth.status === "loading") {
    return <div className="auth-splash">Loading…</div>;
  }
  if (auth.status === "anon") {
    return <div className="auth-splash">Redirecting to sign in…</div>;
  }
  return <RuntimeInner user={auth.user}>{children}</RuntimeInner>;
}

function newThreadId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `t-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function RuntimeInner({
  user,
  children,
}: {
  user: AuthUser;
  children: ReactNode;
}) {
  const agentUrl =
    (process.env.NEXT_PUBLIC_AGUI_AGENT_URL as string | undefined) ??
    "/api/agent";
  const storageKey = `scratchpad.thread.${user.sub}`;

  const threadsRef = useRef<Map<string, StoredThread>>(new Map());
  const [currentThreadId, setCurrentThreadId] = useState<string>(() => {
    if (typeof window !== "undefined") {
      const saved = window.localStorage.getItem(storageKey);
      if (saved) return saved;
    }
    return newThreadId();
  });

  // Persist the active thread id and register it with the backend registry.
  useEffect(() => {
    if (!threadsRef.current.has(currentThreadId)) {
      threadsRef.current.set(currentThreadId, {
        id: currentThreadId,
        messages: [],
      });
    }
    try {
      window.localStorage.setItem(storageKey, currentThreadId);
    } catch {
      /* storage unavailable */
    }
    fetch("/api/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ thread_id: currentThreadId }),
    }).catch(() => {
      /* registry is best-effort */
    });
  }, [currentThreadId, storageKey]);

  const agent = useMemo(() => {
    return new HttpAgent({
      url: agentUrl,
      threadId: currentThreadId,
      headers: { Accept: "text/event-stream" },
    });
  }, [agentUrl, currentThreadId]);

  const threadListAdapter = useMemo(
    () => ({
      threadId: currentThreadId,
      onSwitchToNewThread: async () => {
        const id = newThreadId();
        threadsRef.current.set(id, { id, messages: [] });
        setCurrentThreadId(id);
        console.debug("[agui] Switched to new thread:", id);
      },
      onSwitchToThread: async (threadId: string) => {
        const thread = threadsRef.current.get(threadId);
        if (!thread) {
          throw new Error(`Thread ${threadId} not found`);
        }
        setCurrentThreadId(threadId);
        console.debug("[agui] Switched to thread:", threadId);
        return { messages: thread.messages };
      },
    }),
    [currentThreadId],
  );

  const runtime = useAgUiRuntime({
    agent,
    logger: {
      debug: (...a: any[]) => console.debug("[agui]", ...a),
      error: (...a: any[]) => console.error("[agui]", ...a),
    },
    adapters: {
      threadList: threadListAdapter,
    },
  });

  useEffect(() => {
    return runtime.thread.subscribe(() => {
      threadsRef.current.set(currentThreadId, {
        id: currentThreadId,
        messages: runtime.thread.getState().messages,
      });
    });
  }, [runtime, currentThreadId]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadIdContext.Provider value={currentThreadId}>
        {children}
      </ThreadIdContext.Provider>
    </AssistantRuntimeProvider>
  );
}
