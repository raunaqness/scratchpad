"use client";

import { useEffect, useState } from "react";
import { useAui } from "@assistant-ui/react";

import { useThreadId, useThreadRegistryVersion } from "@/app/MyRuntimeProvider";

type ThreadRow = {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/**
 * Left-hand panel: the user's last 5 threads (see backend/threads_store.py).
 * Opening /app always starts a new thread (MyRuntimeProvider); clicking a row
 * here is the only way back into an older one.
 */
export function ThreadSidebar() {
  const aui = useAui();
  const currentThreadId = useThreadId();
  const registryVersion = useThreadRegistryVersion();
  const [threads, setThreads] = useState<ThreadRow[]>([]);
  const [switchingTo, setSwitchingTo] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/threads?limit=5", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { threads: [] }))
      .then((data: { threads?: ThreadRow[] }) => {
        if (alive) setThreads(data.threads ?? []);
      })
      .catch(() => {
        /* best-effort */
      });
    return () => {
      alive = false;
    };
  }, [registryVersion]);

  async function openThread(threadId: string) {
    if (threadId === currentThreadId || switchingTo) return;
    setSwitchingTo(threadId);
    try {
      await aui.threads.switchToThread(threadId);
    } catch {
      /* row stays clickable to retry */
    } finally {
      setSwitchingTo(null);
    }
  }

  return (
    <aside className="thread-sidebar" aria-label="Recent conversations">
      <button
        type="button"
        className="thread-sidebar-new"
        onClick={() => aui.threads.switchToNewThread()}
      >
        + New thread
      </button>
      <div className="thread-sidebar-list">
        {threads.length === 0 ? (
          <p className="thread-sidebar-empty">No earlier conversations yet.</p>
        ) : (
          threads.map((t) => (
            <button
              key={t.thread_id}
              type="button"
              className="thread-sidebar-item"
              data-active={t.thread_id === currentThreadId || undefined}
              disabled={switchingTo === t.thread_id}
              onClick={() => openThread(t.thread_id)}
            >
              <span className="thread-sidebar-item-title">
                {t.title || "Untitled conversation"}
              </span>
              <span className="thread-sidebar-item-time">
                {switchingTo === t.thread_id ? "Opening…" : relativeTime(t.updated_at)}
              </span>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}
