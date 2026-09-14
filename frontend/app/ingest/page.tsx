"use client";

import { CheckCircle2, ExternalLink, Loader2, Trash2, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

export const dynamic = "force-dynamic";

const POLL_MS = 1500;
const GRAPH_BROWSER_URL = "http://localhost:3010";

type Item = {
  url: string;
  title: string | null;
  published_at: string | null;
  selected: boolean;
  stage: string;
  error: string | null;
};

type Run = {
  run_id: number;
  source_type: string;
  source_ref: string;
  discovery_method: string | null;
  status: string;
  max_items: number;
  items: Item[];
};

type LibraryItem = {
  id: number;
  url: string;
  title: string | null;
  published_at: string | null;
  source_ref: string;
};

type Library = {
  count: number;
  max: number;
  remaining: number;
  items: LibraryItem[];
  active_run: Run | null;
};

type Source = { title: string | null; url: string | null };
type QueryResult = { fact: string; valid_at: string | null; sources: Source[] };

const RUNNING_STATUSES = new Set(["scraping", "ingesting"]);

const STAGE_LABELS: Record<string, string> = {
  discovered: "Waiting",
  queued: "Queued",
  fetching: "Fetching",
  fetched: "Fetched",
  extracting: "Extracting",
  extracted: "Extracted",
  storing: "Storing",
  stored: "Stored",
  ingesting: "Adding to knowledge graph",
  ingested: "Done",
  failed: "Failed",
  skipped: "Skipped",
};

function label(item: Item): string {
  return STAGE_LABELS[item.stage] ?? item.stage;
}

export default function IngestPage() {
  const [library, setLibrary] = useState<Library | null>(null);
  const [loadingLibrary, setLoadingLibrary] = useState(true);

  const [showIngestForm, setShowIngestForm] = useState(false);
  const [url, setUrl] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<number | null>(null);

  const [question, setQuestion] = useState("");
  const [results, setResults] = useState<QueryResult[] | null>(null);
  const [queryBusy, setQueryBusy] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const loadLibrary = useCallback(async () => {
    const res = await fetch("/api/ingest/library", { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as Library;
    setLibrary(data);
    return data;
  }, []);

  const pollRun = useCallback(
    (runId: number) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        const res = await fetch(`/api/ingest/runs/${runId}`, { cache: "no-store" });
        if (!res.ok) return;
        const data = (await res.json()) as Run;
        setRun(data);
        if (!RUNNING_STATUSES.has(data.status)) {
          stopPolling();
          // Whatever happened (done or failed), the library's article count
          // and slot math may have changed — refresh it.
          loadLibrary();
        }
      }, POLL_MS);
    },
    [stopPolling, loadLibrary],
  );

  // The knowledge graph is account-wide: on every visit, just ask "what does
  // this account already have?" — no run id or URL param to remember, and
  // any run still mid-flight (scraping/awaiting confirmation) picks back up
  // automatically instead of vanishing on refresh.
  useEffect(() => {
    let alive = true;
    (async () => {
      const data = await loadLibrary();
      if (!alive) return;
      if (data?.active_run) {
        const activeRun = data.active_run;
        setRun(activeRun);
        setSelection(new Set(activeRun.items.filter((i) => i.selected).map((i) => i.url)));
        if (RUNNING_STATUSES.has(activeRun.status)) pollRun(activeRun.run_id);
      }
      setLoadingLibrary(false);
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleDiscover() {
    if (!url.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/ingest/blog/discover", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      const data = (await res.json()) as Run & { error?: string };
      if (!res.ok) {
        setError(data.error ?? "Could not discover posts for that URL.");
        return;
      }
      setRun(data);
      setSelection(new Set(data.items.filter((i) => i.selected).map((i) => i.url)));
    } catch {
      setError("Something went wrong reaching the ingest service.");
    } finally {
      setBusy(false);
    }
  }

  function toggleItem(itemUrl: string) {
    if (!run) return;
    setSelection((prev) => {
      const next = new Set(prev);
      if (next.has(itemUrl)) {
        next.delete(itemUrl);
      } else {
        if (next.size >= run.max_items) return prev;
        next.add(itemUrl);
      }
      return next;
    });
  }

  async function handleConfirm() {
    if (!run || selection.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      const selectedUrls = Array.from(selection);
      const patchRes = await fetch(`/api/ingest/runs/${run.run_id}/selection`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ selected_urls: selectedUrls }),
      });
      if (!patchRes.ok) {
        const data = await patchRes.json();
        setError(data.error ?? "Could not save your selection.");
        return;
      }
      const startRes = await fetch(`/api/ingest/runs/${run.run_id}/start`, {
        method: "POST",
      });
      const startData = await startRes.json();
      if (!startRes.ok) {
        setError(startData.error ?? "Could not start scraping.");
        return;
      }
      setRun((prev) => (prev ? { ...prev, status: "scraping" } : prev));
      setShowIngestForm(false);
      pollRun(run.run_id);
    } catch {
      setError("Something went wrong starting the run.");
    } finally {
      setBusy(false);
    }
  }

  async function handleQuery() {
    if (!question.trim()) return;
    setQueryBusy(true);
    setResults(null);
    try {
      const res = await fetch("/api/ingest/query", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: question.trim() }),
      });
      const data = await res.json();
      if (res.ok) setResults(data.results ?? []);
      else setError(data.error ?? "Query failed.");
    } catch {
      setError("Something went wrong running that query.");
    } finally {
      setQueryBusy(false);
    }
  }

  async function handleRemove(itemId: number) {
    setRemovingId(itemId);
    setError(null);
    try {
      const res = await fetch(`/api/ingest/items/${itemId}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error ?? "Could not remove that article.");
        return;
      }
      setLibrary(data as Library);
    } catch {
      setError("Something went wrong removing that article.");
    } finally {
      setRemovingId(null);
    }
  }

  function cancelIngestFlow() {
    stopPolling();
    setRun(null);
    setSelection(new Set());
    setShowIngestForm(false);
    setUrl("");
    setError(null);
  }

  const inFlight = run && (run.status === "awaiting_confirmation" || RUNNING_STATUSES.has(run.status));

  return (
    <main className="ingest-page">
      <p className="detail-kicker">Ingest</p>
      <h1>Your knowledge base</h1>
      <p className="ingest-intro">
        Blogs you&rsquo;ve brought in, read into one knowledge graph you can
        ask questions against — up to 15 articles at a time.
      </p>

      {error ? <p className="ingest-error">{error}</p> : null}

      {loadingLibrary ? (
        <p className="ingest-intro">
          <Loader2 className="ingest-spin" size={16} /> Loading…
        </p>
      ) : null}

      {!loadingLibrary && !inFlight ? (
        <>
          <div className="ingest-candidates-head">
            <p>
              <strong>{library?.count ?? 0}</strong> / {library?.max ?? 15} articles
              ingested
            </p>
            <a
              className="text-action ingest-graph-link"
              href={GRAPH_BROWSER_URL}
              target="_blank"
              rel="noreferrer"
            >
              Knowledge graph <ExternalLink size={13} />
            </a>
          </div>

          {library && library.items.length > 0 ? (
            <ul className="ingest-library-list">
              {library.items.map((item) => (
                <li key={item.id} className="ingest-library-row">
                  <span className="ingest-candidate-body">
                    <span className="ingest-candidate-title">
                      {item.title || item.url}
                    </span>
                    <span className="ingest-candidate-meta">
                      {new URL(item.source_ref).hostname}
                    </span>
                  </span>
                  <button
                    type="button"
                    className="ingest-remove-btn"
                    disabled={removingId === item.id}
                    onClick={() => handleRemove(item.id)}
                    aria-label={`Remove ${item.title || item.url}`}
                    title="Remove this article"
                  >
                    {removingId === item.id ? (
                      <Loader2 className="ingest-spin" size={15} />
                    ) : (
                      <Trash2 size={15} />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ingest-query-empty">Nothing ingested yet.</p>
          )}

          {!showIngestForm ? (
            library && library.remaining > 0 ? (
              <button
                type="button"
                className="primary-action"
                onClick={() => setShowIngestForm(true)}
              >
                Ingest more
              </button>
            ) : (
              <p className="ingest-cap-notice">
                You&rsquo;re at the 15-article limit. Remove some articles above
                to ingest more.
              </p>
            )
          ) : (
            <div className="ingest-candidates">
              <p className="ingest-intro">
                {library?.remaining ?? 15} slot{(library?.remaining ?? 15) === 1 ? "" : "s"}{" "}
                left. Paste another blog&rsquo;s URL.
              </p>
              <div className="ingest-form">
                <input
                  className="ingest-url-input"
                  type="url"
                  placeholder="https://company.com/blog"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleDiscover()}
                  disabled={busy}
                />
                <button
                  type="button"
                  className="primary-action"
                  disabled={busy || !url.trim()}
                  onClick={handleDiscover}
                >
                  {busy ? <Loader2 className="ingest-spin" size={16} /> : null}
                  Find posts
                </button>
              </div>
              <button type="button" className="text-action" onClick={cancelIngestFlow}>
                Cancel
              </button>
            </div>
          )}

          {library && library.count > 0 ? (
            <div className="ingest-query">
              <h2>Ask the knowledge graph</h2>
              <div className="ingest-form">
                <input
                  className="ingest-url-input"
                  type="text"
                  placeholder="What does this blog say about…"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleQuery()}
                  disabled={queryBusy}
                />
                <button
                  type="button"
                  className="primary-action"
                  disabled={queryBusy || !question.trim()}
                  onClick={handleQuery}
                >
                  {queryBusy ? <Loader2 className="ingest-spin" size={16} /> : null}
                  Ask
                </button>
              </div>
              {results ? (
                results.length === 0 ? (
                  <p className="ingest-query-empty">No facts matched that question.</p>
                ) : (
                  <ul className="ingest-results-list">
                    {results.map((r, i) => (
                      <li key={i}>
                        <p>{r.fact}</p>
                        {r.sources.length > 0 ? (
                          <p className="ingest-result-sources">
                            {r.sources.map((s, j) => (
                              <span key={j}>
                                {j > 0 ? ", " : ""}
                                {s.url ? (
                                  <a href={s.url} target="_blank" rel="noreferrer">
                                    {s.title || s.url}
                                  </a>
                                ) : (
                                  s.title
                                )}
                              </span>
                            ))}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}

      {run && run.status === "awaiting_confirmation" ? (
        <div className="ingest-candidates">
          <div className="ingest-candidates-head">
            <p>
              Found {run.items.length} post{run.items.length === 1 ? "" : "s"} on{" "}
              <strong>{new URL(run.source_ref).hostname}</strong> ({run.discovery_method}).
              Pick up to {run.max_items} to read.
            </p>
            <span className="ingest-selection-count">
              {selection.size} / {run.max_items} selected
            </span>
          </div>
          <ul className="ingest-candidate-list">
            {run.items.map((item) => {
              const checked = selection.has(item.url);
              const disabled = !checked && selection.size >= run.max_items;
              return (
                <li key={item.url}>
                  <label
                    className={`ingest-candidate${disabled ? " is-disabled" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => toggleItem(item.url)}
                    />
                    <span className="ingest-candidate-body">
                      <span className="ingest-candidate-title">
                        {item.title || item.url}
                      </span>
                      <span className="ingest-candidate-meta">
                        {item.published_at
                          ? new Date(item.published_at).toLocaleDateString()
                          : null}{" "}
                        {item.url}
                      </span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          <div className="ingest-candidates-actions">
            <button
              type="button"
              className="primary-action"
              disabled={busy || selection.size === 0}
              onClick={handleConfirm}
            >
              {busy ? <Loader2 className="ingest-spin" size={16} /> : null}
              Confirm & start reading {selection.size} post
              {selection.size === 1 ? "" : "s"}
            </button>
            <button type="button" className="text-action" onClick={cancelIngestFlow}>
              Start over
            </button>
          </div>
        </div>
      ) : null}

      {run && RUNNING_STATUSES.has(run.status) ? (
        <div className="ingest-progress">
          <div className="ingest-candidates-head">
            <p>
              Reading from <strong>{new URL(run.source_ref).hostname}</strong>
            </p>
            <span className="progress-chip">
              <span className="progress-chip-spinner" />
              <span className="progress-chip-label">{run.status}</span>
            </span>
          </div>
          <ul className="ingest-progress-list">
            {run.items
              .filter((i) => i.selected)
              .map((item) => (
                <li key={item.url} className="ingest-progress-row">
                  {item.stage === "failed" ? (
                    <XCircle size={16} className="ingest-stage-icon is-failed" />
                  ) : item.stage === "ingested" ? (
                    <CheckCircle2 size={16} className="ingest-stage-icon is-done" />
                  ) : (
                    <Loader2 size={16} className="ingest-spin ingest-stage-icon" />
                  )}
                  <span className="ingest-candidate-title">
                    {item.title || item.url}
                  </span>
                  <span className="ingest-stage-label">
                    {label(item)}
                    {item.error ? ` — ${item.error}` : ""}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      ) : null}

      {run && run.status === "failed" ? (
        <div className="ingest-progress">
          <p className="ingest-error">That run failed. Nothing was added to your library.</p>
          <button type="button" className="text-action" onClick={cancelIngestFlow}>
            Back to your library
          </button>
        </div>
      ) : null}
    </main>
  );
}
