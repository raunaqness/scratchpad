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
  fromThreadMessageLike,
  type ThreadMessage,
} from "@assistant-ui/react";
import { HttpAgent } from "@ag-ui/client";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import type { ReadonlyJSONObject } from "assistant-stream/utils";

import { AuthProvider, useAuth, type AuthUser } from "@/app/auth-context";

const ThreadIdContext = createContext<string | null>(null);

/** The active thread id, for callers off the AG-UI stream (e.g. skill runs). */
export function useThreadId(): string {
  const id = useContext(ThreadIdContext);
  if (!id) throw new Error("useThreadId outside MyRuntimeProvider");
  return id;
}

/**
 * Bumps whenever the thread registry may have changed (a new thread was
 * registered, or a run on the current thread just finished) — the sidebar
 * refetches `/api/threads` when this changes instead of polling.
 */
const ThreadRegistryVersionContext = createContext<number>(0);

export function useThreadRegistryVersion(): number {
  return useContext(ThreadRegistryVersionContext);
}

type StoredThread = {
  id: string;
  messages: readonly ThreadMessage[];
};

type ThreadStateSnapshot = ReadonlyJSONObject;

/**
 * AG-UI runtime, gated on a Google session. Every mount of `/app` starts a
 * fresh thread — the sidebar (see `components/thread-sidebar.tsx`) is what
 * lets the user deliberately resume one of their last few threads. The
 * backend registers each thread on its first turn.
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

  const threadsRef = useRef<Map<string, StoredThread>>(new Map());
  // Every mount starts a brand-new thread — resuming an older one is only
  // ever a deliberate sidebar click (see onSwitchToThread below).
  const [currentThreadId, setCurrentThreadId] = useState<string>(newThreadId);
  const [registryVersion, setRegistryVersion] = useState(0);
  const bumpRegistry = () => setRegistryVersion((v) => v + 1);

  // Register the active thread with the backend registry.
  useEffect(() => {
    if (!threadsRef.current.has(currentThreadId)) {
      threadsRef.current.set(currentThreadId, {
        id: currentThreadId,
        messages: [],
      });
    }
    fetch("/api/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ thread_id: currentThreadId }),
    })
      .then(bumpRegistry)
      .catch(() => {
        /* registry is best-effort */
      });
  }, [currentThreadId]);

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
        let thread = threadsRef.current.get(threadId);
        let state: ThreadStateSnapshot | undefined;
        if (!thread) {
          const res = await fetch(`/api/threads/${threadId}/messages`);
          if (!res.ok) {
            throw new Error(`Thread ${threadId} not found`);
          }
          const data = (await res.json()) as {
            messages?: { role: string; content: string }[];
            state?: ThreadStateSnapshot;
          };
          const messages = (data.messages ?? []).map((m, i) =>
            fromThreadMessageLike(
              {
                role: m.role as "user" | "assistant" | "system",
                content: m.content,
              },
              `${threadId}-${i}`,
              { type: "complete", reason: "stop" },
            ),
          );
          thread = { id: threadId, messages };
          threadsRef.current.set(threadId, thread);
          state = data.state;
          console.debug("[agui] Loaded thread history from backend:", threadId);
        } else {
          console.debug("[agui] Switched to thread:", threadId);
        }
        setCurrentThreadId(threadId);
        return { messages: thread.messages, state };
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

  const wasRunningRef = useRef(false);
  useEffect(() => {
    return runtime.thread.subscribe(() => {
      const state = runtime.thread.getState();
      threadsRef.current.set(currentThreadId, {
        id: currentThreadId,
        messages: state.messages,
      });
      // Falling edge only — the backend sets the thread's real title
      // (from the first message) once a run finishes.
      if (wasRunningRef.current && !state.isRunning) {
        bumpRegistry();
      }
      wasRunningRef.current = state.isRunning;
    });
  }, [runtime, currentThreadId]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadIdContext.Provider value={currentThreadId}>
        <ThreadRegistryVersionContext.Provider value={registryVersion}>
          {children}
        </ThreadRegistryVersionContext.Provider>
      </ThreadIdContext.Provider>
    </AssistantRuntimeProvider>
  );
}
