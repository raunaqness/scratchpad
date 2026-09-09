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
  AuiConfig,
  AuiProvider,
  Suggestions,
  Tools,
  defineToolkit,
  useAui,
  useAuiEvent,
} from "@assistant-ui/react";
import { useAgUiState } from "@assistant-ui/react-ag-ui";
import { Check, PlusIcon } from "lucide-react";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { MarkdownPreview } from "@/components/markdown-preview";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/app/auth-context";
import { useThreadId } from "@/app/MyRuntimeProvider";
import { cn } from "@/lib/utils";

type ChoiceOption = { id: string; label: string };
type ChoiceArgs = { id?: string; question?: string; options?: ChoiceOption[] };
type ChoiceToolProps = { args: ChoiceArgs; result?: unknown };

type SignalProgress = {
  node?: string | null;
  label?: string;
  status?: string;
  steps?: string[];
  done?: boolean;
};

type VersionItem = {
  seq: number;
  title?: string;
  topic?: string;
  body?: string;
  angles?: string[];
  outline?: string[];
  open_questions?: string[];
  created_at?: string;
};

type DerivedItem = {
  id: string;
  skill_id: string;
  skill_name: string;
  title?: string;
  body: string;
  from_version: number;
  open_questions?: string[];
  created_at?: string;
};

type SignalState = {
  processing?: boolean;
  progress?: SignalProgress;
  status?: string;
  title?: string;
  topic?: string;
  angles?: string[];
  outline?: string[];
  body?: string;
  open_questions?: string[];
  version?: number;
  versions?: VersionItem[];
  head?: number;
  derived?: DerivedItem[];
  active_tab?: string | null;
  // creative follow-up agent is running (post-processing phase)
  follow_up_pending?: boolean;
  // credits (set on a snapshot only when a turn is refused)
  credits_balance?: number | null;
  out_of_credits?: boolean;
  // back-compat mirrors
  draft?: string;
  draft_version?: number;
};

/**
 * Agent-generated picker. The backend emits a `request_choice` tool call
 * (e.g. brainstorm angles); a selection is sent back as a normal user turn.
 */
function ChoiceTool({ args, result }: ChoiceToolProps) {
  const aui = useAui();
  const [selected, setSelected] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const options = args.options ?? [];
  const done = result !== undefined || submitted;

  if (done) {
    const label =
      options.find((o) => o.id === selected)?.label ??
      (typeof result === "string" ? result : selected);
    return (
      <div className="choice-card choice-card-complete">
        <Check size={16} />
        <span>Going with: {label || "your pick"}</span>
      </div>
    );
  }

  return (
    <div className="choice-card">
      <p className="choice-question">{args.question ?? "Choose an option"}</p>
      <div className="choice-options">
        {options.map((option) => (
          <label className="choice-option" key={option.id}>
            <input
              type="radio"
              name={`choice-${args.id ?? "option"}`}
              value={option.id}
              checked={selected === option.id}
              onChange={() => setSelected(option.id)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
      <button
        type="button"
        className="choice-proceed"
        disabled={!selected}
        onClick={() => {
          const label = options.find((o) => o.id === selected)?.label;
          if (!label) return;
          setSubmitted(true);
          aui.thread.append(`Let's run with this angle: ${label}`);
        }}
      >
        Proceed
      </button>
    </div>
  );
}

type FollowUpItem = { label: string; kind?: string };
type FollowUpArgs = { items?: FollowUpItem[] };

/**
 * Creative agent output: 3-5 next-move buttons. Clicking one sends its label as
 * the user's next message. Once any is used the group is spent.
 */
function FollowUpButtons({ args }: { args: FollowUpArgs }) {
  const aui = useAui();
  const [spent, setSpent] = useState(false);
  const items = (args.items ?? []).filter((i) => i && i.label);
  if (!items.length) return null;

  return (
    <div className="follow-up-card" aria-label="Suggested next moves">
      <span className="follow-up-card-label">Next moves</span>
      <div className="follow-up-options">
        {items.map((item, i) => (
          <button
            key={`${item.label}-${i}`}
            type="button"
            className="follow-up-btn"
            data-kind={item.kind ?? "direction"}
            disabled={spent}
            onClick={() => {
              setSpent(true);
              aui.thread.append(item.label);
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}

const toolkit = defineToolkit({
  request_choice: {
    type: "backend",
    render: (props) => (
      <ChoiceTool args={props.args as ChoiceArgs} result={props.result} />
    ),
  },
  follow_up: {
    type: "backend",
    render: (props) => <FollowUpButtons args={props.args as FollowUpArgs} />,
  },
});

const STATUS_LABEL: Record<string, string> = {
  empty: "Empty",
  notes: "Notes",
  developing: "Developing",
  stable: "Stable",
};

const RITUAL_PHRASES = [
  "Reading your message",
  "Thinking it through",
  "Working on the scratchpad",
  "Reviewing",
  "Finishing up",
];

const SKILLS: { id: string; label: string }[] = [
  { id: "blog_outline", label: "Blog outline" },
  { id: "social_post", label: "Social post" },
  { id: "marketing_campaign", label: "Marketing campaign" },
];
const SKILL_LABEL: Record<string, string> = Object.fromEntries(
  SKILLS.map((s) => [s.id, s.label]),
);

// ---------------------------------------------------------------------------
// generated artifacts (standalone skill runs, off the chat turn)
// ---------------------------------------------------------------------------

type StoredArtifact = {
  id: number;
  skill_id: string;
  version: number;
  body: string;
  created_at: string;
};

type ArtifactsValue = {
  bySkill: Map<string, StoredArtifact[]>; // newest first
  streamingSkill: string | null;
  streamingText: string;
  error: string | null;
  generate: (skillId: string) => void;
};

const ArtifactsContext = createContext<ArtifactsValue | null>(null);
const useArtifacts = (): ArtifactsValue => {
  const ctx = useContext(ArtifactsContext);
  if (!ctx) throw new Error("useArtifacts outside provider");
  return ctx;
};

function ArtifactsProvider({ children }: { children: ReactNode }) {
  const threadId = useThreadId();
  const [artifacts, setArtifacts] = useState<StoredArtifact[]>([]);
  const [streamingSkill, setStreamingSkill] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const hydrate = useRef(() => {});
  hydrate.current = () => {
    fetch(`/api/artifacts?thread_id=${encodeURIComponent(threadId)}`, {
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setArtifacts(Array.isArray(d?.artifacts) ? d.artifacts : []))
      .catch(() => {});
  };

  useEffect(() => {
    setArtifacts([]);
    setStreamingSkill(null);
    setStreamingText("");
    setError(null);
    hydrate.current();
  }, [threadId]);

  const generate = (skillId: string) => {
    if (streamingSkill) return;
    setError(null);
    setStreamingText("");
    setStreamingSkill(skillId);
    (async () => {
      try {
        const res = await fetch("/api/artifacts/generate", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ thread_id: threadId, skill_id: skillId }),
        });
        if (!res.ok || !res.body) {
          setError(res.status === 402 ? "out_of_credits" : "failed");
          setStreamingSkill(null);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split("\n\n");
          buf = lines.pop() ?? "";
          for (const line of lines) {
            const m = line.match(/^data: (.*)$/s);
            if (!m) continue;
            let evt: { type?: string; text?: string };
            try {
              evt = JSON.parse(m[1]);
            } catch {
              continue;
            }
            if (evt.type === "delta" && evt.text) {
              setStreamingText((prev) => prev + evt.text);
            } else if (evt.type === "error") {
              setError("failed");
            }
          }
        }
      } catch {
        setError("failed");
      } finally {
        setStreamingSkill(null);
        setStreamingText("");
        hydrate.current();
      }
    })();
  };

  const value = useMemo<ArtifactsValue>(() => {
    const bySkill = new Map<string, StoredArtifact[]>();
    for (const a of artifacts) {
      const list = bySkill.get(a.skill_id) ?? [];
      list.push(a);
      bySkill.set(a.skill_id, list);
    }
    return { bySkill, streamingSkill, streamingText, error, generate };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifacts, streamingSkill, streamingText, error]);

  return (
    <ArtifactsContext.Provider value={value}>
      {children}
    </ArtifactsContext.Provider>
  );
}

function ArtifactRitual() {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    setIndex(0);
    const id = setInterval(
      () => setIndex((p) => Math.min(p + 1, RITUAL_PHRASES.length - 1)),
      1400,
    );
    return () => clearInterval(id);
  }, []);
  return (
    <div className="artifact-ritual" aria-live="polite">
      <span className="artifact-ritual-dot" />
      <span>{RITUAL_PHRASES[index]}…</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// version history (scratchpad only)
// ---------------------------------------------------------------------------

type HistoryValue = {
  count: number;
  versions: VersionItem[];
  previewSeq: number | null;
  isPreviewing: boolean;
  armedBase: number | null;
  displaySeq: number;
  step: (delta: number) => void;
  goToLatest: () => void;
  openBranchModal: () => void;
};

const HistoryContext = createContext<HistoryValue | null>(null);
const useHistory = (): HistoryValue => {
  const ctx = useContext(HistoryContext);
  if (!ctx) throw new Error("useHistory outside provider");
  return ctx;
};

function ArtifactHistoryProvider({ children }: { children: ReactNode }) {
  const aui = useAui();
  const state = useAgUiState<SignalState>();
  const count = state?.head ?? 0;
  const versions = useMemo(() => state?.versions ?? [], [state?.versions]);

  const [previewSeq, setPreviewSeq] = useState<number | null>(null);
  const [armedBase, setArmedBase] = useState<number | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const clearBranch = useRef(() => {});
  clearBranch.current = () => {
    setArmedBase(null);
    try {
      aui.composer.setRunConfig({});
    } catch {
      /* composer not ready */
    }
  };

  useAuiEvent("thread.runEnd", () => {
    setPreviewSeq(null);
    clearBranch.current();
  });
  useAuiEvent("threads.selectionChanged", () => {
    setPreviewSeq(null);
    clearBranch.current();
  });

  useEffect(() => {
    if (previewSeq !== null && (previewSeq >= count || previewSeq < 1)) {
      setPreviewSeq(null);
    }
  }, [count, previewSeq]);

  const value = useMemo<HistoryValue>(() => {
    const displaySeq = previewSeq ?? count;
    return {
      count,
      versions,
      previewSeq,
      isPreviewing: previewSeq !== null && previewSeq < count,
      armedBase,
      displaySeq,
      step: (delta) => {
        clearBranch.current();
        setPreviewSeq((prev) => {
          const current = prev ?? count;
          const next = Math.min(Math.max(current + delta, 1), count);
          return next >= count ? null : next;
        });
      },
      goToLatest: () => {
        clearBranch.current();
        setPreviewSeq(null);
      },
      openBranchModal: () => setModalOpen(true),
    };
  }, [count, versions, previewSeq, armedBase]);

  const base = previewSeq ?? count;

  return (
    <HistoryContext.Provider value={value}>
      {children}
      {modalOpen ? (
        <OverwriteModal
          base={base}
          count={count}
          onCancel={() => setModalOpen(false)}
          onConfirm={() => {
            setModalOpen(false);
            setArmedBase(base);
            try {
              aui.composer.setRunConfig({ custom: { base_version: base } });
            } catch {
              /* composer not ready */
            }
          }}
        />
      ) : null}
    </HistoryContext.Provider>
  );
}

function OverwriteModal({
  base,
  count,
  onCancel,
  onConfirm,
}: {
  base: number;
  count: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const discarded = count - base === 1 ? `v${base + 1}` : `v${base + 1}–v${count}`;
  return (
    <div
      className="overwrite-modal-backdrop"
      role="dialog"
      aria-modal="true"
      onClick={onCancel}
    >
      <div className="overwrite-modal-card" onClick={(e) => e.stopPropagation()}>
        <h3>Continue from v{base}?</h3>
        <p>
          {discarded} will be discarded. Your next message creates a new v
          {base + 1} from v{base}.
        </p>
        <div className="overwrite-modal-actions">
          <button type="button" className="overwrite-modal-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="overwrite-modal-confirm"
            onClick={onConfirm}
          >
            Continue from v{base}
          </button>
        </div>
      </div>
    </div>
  );
}

function VersionNav() {
  const history = useHistory();
  if (history.count < 1) return null;
  return (
    <div className="version-nav">
      <button
        type="button"
        onClick={() => history.step(-1)}
        disabled={history.displaySeq <= 1}
        aria-label="Older version"
      >
        ‹
      </button>
      <span className="version-nav-label">
        v{history.displaySeq}
        <span className="version-nav-total"> / v{history.count}</span>
      </span>
      <button
        type="button"
        onClick={() => history.step(1)}
        disabled={history.displaySeq >= history.count}
        aria-label="Newer version"
      >
        ›
      </button>
    </div>
  );
}

function PreviewBanner() {
  const history = useHistory();
  if (!history.isPreviewing) return null;
  const seq = history.previewSeq as number;

  if (history.armedBase !== null) {
    return (
      <div className="artifact-preview-banner is-armed">
        <span>
          Editing from v{seq} — your next message creates v{seq + 1}.
        </span>
        <button type="button" onClick={() => history.goToLatest()}>
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div className="artifact-preview-banner">
      <span>
        Viewing v{seq} of {history.count} · read-only
      </span>
      <span className="artifact-preview-banner-actions">
        <button type="button" onClick={() => history.openBranchModal()}>
          Edit from v{seq}
        </button>
        <button type="button" onClick={() => history.goToLatest()}>
          Back to latest
        </button>
      </span>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="artifact-copy-btn"
      disabled={!text}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable */
        }
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function SkillBar() {
  const state = useAgUiState<SignalState>();
  const history = useHistory();
  const artifacts = useArtifacts();
  const processing = Boolean(state?.processing);
  const hasContent = Boolean(state?.body || (state?.angles?.length ?? 0));
  const busy = artifacts.streamingSkill !== null;
  const disabled = processing || history.isPreviewing || !hasContent || busy;

  return (
    <div className="skill-bar">
      <span className="skill-bar-label">Build from this:</span>
      {SKILLS.map((skill) => (
        <button
          key={skill.id}
          type="button"
          disabled={disabled}
          onClick={() => artifacts.generate(skill.id)}
        >
          {artifacts.streamingSkill === skill.id ? "Generating…" : skill.label}
        </button>
      ))}
      {artifacts.error === "out_of_credits" ? (
        <span className="skill-bar-error">Out of credits</span>
      ) : artifacts.error ? (
        <span className="skill-bar-error">Generation failed — try again</span>
      ) : null}
    </div>
  );
}

function ScratchpadPane() {
  const state = useAgUiState<SignalState>();
  const history = useHistory();
  const processing = Boolean(state?.processing);
  const previewing = history.isPreviewing;
  const buildTurn = processing && state?.progress?.node === "build";
  const showRitual = processing && !previewing && !buildTurn;

  const preview = previewing
    ? history.versions[(history.previewSeq as number) - 1]
    : undefined;

  const body = preview ? preview.body ?? "" : state?.body ?? state?.draft ?? "";
  const angles = preview ? preview.angles ?? [] : state?.angles ?? [];
  const outline = preview ? preview.outline ?? [] : state?.outline ?? [];
  const openQuestions = preview
    ? preview.open_questions ?? []
    : state?.open_questions ?? [];
  const title = preview ? preview.title : state?.title;

  const hasContent = Boolean(body || outline.length || angles.length);

  return (
    <section
      className={cn(
        "artifact-panel",
        showRitual && "is-loading",
        previewing && "is-previewing",
      )}
    >
      <div className="artifact-header">
        <h2>{title || "Scratchpad"}</h2>
        <VersionNav />
      </div>

      <div
        className="artifact-progressbar"
        aria-hidden={!showRitual}
        data-active={showRitual}
      />

      {showRitual ? <ArtifactRitual /> : null}
      <PreviewBanner />
      <SkillBar />

      <div className="artifact-body">
        {body ? (
          <MarkdownPreview
            text={body}
            streaming={showRitual}
            className="artifact-copy"
          />
        ) : outline.length ? (
          <ol className="artifact-outline">
            {outline.map((beat, i) => (
              <li key={`${beat}-${i}`}>{beat}</li>
            ))}
          </ol>
        ) : angles.length ? (
          <ul className="artifact-angles">
            {angles.map((angle, i) => (
              <li key={`${angle}-${i}`}>{angle}</li>
            ))}
          </ul>
        ) : showRitual ? (
          <div className="artifact-skeleton">
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-4/5" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        ) : (
          <div className="artifact-empty">
            <div className="artifact-empty-mark">S</div>
            <p>Start jotting.</p>
            <span>
              Dump a raw idea here and rework it. When it feels right, build
              something from it.
            </span>
          </div>
        )}

        {openQuestions.length ? (
          <div className="artifact-questions">
            <p className="artifact-questions-heading">Open questions</p>
            <ul>
              {openQuestions.map((q, i) => (
                <li key={`${q}-${i}`}>{q}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <div className="artifact-footer">
        <span>
          {previewing
            ? `Version ${history.previewSeq} of ${history.count}`
            : processing
              ? "Working…"
              : hasContent
                ? STATUS_LABEL[state?.status ?? ""] ?? "Notes"
                : "Empty"}
        </span>
        <CopyButton text={body} />
      </div>
    </section>
  );
}

function DerivedView({
  derived,
  building,
}: {
  derived: DerivedItem;
  building: boolean;
}) {
  return (
    <section className="artifact-panel derived-panel">
      <div className="artifact-body">
        <MarkdownPreview
          text={derived.body}
          streaming={building}
          className="derived-body"
        />
        {derived.open_questions?.length ? (
          <div className="artifact-questions">
            <p className="artifact-questions-heading">Unverified in this output</p>
            <ul>
              {derived.open_questions.map((q, i) => (
                <li key={`${q}-${i}`}>{q}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <div className="artifact-footer">
        <span>
          {building
            ? "Building…"
            : `Built from scratchpad v${derived.from_version}`}
        </span>
        <CopyButton text={derived.body} />
      </div>
    </section>
  );
}

function GeneratedArtifactView({ skillId }: { skillId: string }) {
  const artifacts = useArtifacts();
  const versions = artifacts.bySkill.get(skillId) ?? []; // newest first
  const streaming = artifacts.streamingSkill === skillId;
  const [picked, setPicked] = useState<number | null>(null);

  const latest = versions[0];
  const current =
    picked != null ? versions.find((v) => v.version === picked) : latest;

  const text = streaming
    ? artifacts.streamingText
    : current?.body ?? "";

  return (
    <section className="artifact-panel derived-panel">
      <div className="artifact-body">
        <MarkdownPreview
          text={text || (streaming ? "…" : "")}
          streaming={streaming}
          className="derived-body"
        />
      </div>
      <div className="artifact-footer">
        <span>
          {streaming ? (
            "Generating…"
          ) : versions.length > 1 ? (
            <label className="artifact-version-select">
              Version{" "}
              <select
                value={(current ?? latest)?.version ?? 1}
                onChange={(e) => setPicked(Number(e.target.value))}
              >
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    v{v.version}
                  </option>
                ))}
              </select>
            </label>
          ) : current ? (
            `Version ${current.version}`
          ) : (
            ""
          )}
        </span>
        <CopyButton text={text} />
      </div>
    </section>
  );
}

function WorkspacePanel() {
  const state = useAgUiState<SignalState>();
  const artifacts = useArtifacts();
  const derived = useMemo(() => state?.derived ?? [], [state?.derived]);
  const building = Boolean(state?.processing) && state?.progress?.node === "build";

  // skills that have a generated artifact or are mid-generation → one tab each
  const genSkills = useMemo(() => {
    const ids = new Set<string>(artifacts.bySkill.keys());
    if (artifacts.streamingSkill) ids.add(artifacts.streamingSkill);
    return SKILLS.map((s) => s.id).filter((id) => ids.has(id));
  }, [artifacts.bySkill, artifacts.streamingSkill]);

  const [activeTab, setActiveTab] = useState<string>("scratchpad");
  const ids = derived.map((d) => d.id).join("|");
  useEffect(() => {
    if (artifacts.streamingSkill) setActiveTab(`gen:${artifacts.streamingSkill}`);
    else if (derived.length) setActiveTab(derived[derived.length - 1].id);
  }, [ids, derived.length, artifacts.streamingSkill]);

  const activeDerived = derived.find((d) => d.id === activeTab);
  const activeGen = activeTab.startsWith("gen:") ? activeTab.slice(4) : null;
  const onScratchpad = !activeDerived && !activeGen;

  const hasTabs = derived.length > 0 || genSkills.length > 0;
  const isOpen = Boolean(
    state?.processing ||
      state?.body ||
      state?.angles?.length ||
      state?.outline?.length ||
      state?.head ||
      hasTabs,
  );
  if (!isOpen) return null;

  return (
    <div className="workspace-panel">
      {hasTabs ? (
        <div className="workspace-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={onScratchpad}
            className={cn("workspace-tab", onScratchpad && "is-active")}
            onClick={() => setActiveTab("scratchpad")}
          >
            Scratchpad
          </button>
          {genSkills.map((id) => (
            <button
              key={`gen:${id}`}
              type="button"
              role="tab"
              aria-selected={activeGen === id}
              className={cn("workspace-tab", activeGen === id && "is-active")}
              onClick={() => setActiveTab(`gen:${id}`)}
            >
              {SKILL_LABEL[id] ?? id}
              {artifacts.streamingSkill === id ? (
                <span className="workspace-tab-meta"> · generating…</span>
              ) : null}
            </button>
          ))}
          {derived.map((d) => (
            <button
              key={d.id}
              type="button"
              role="tab"
              aria-selected={d.id === activeTab}
              className={cn("workspace-tab", d.id === activeTab && "is-active")}
              onClick={() => setActiveTab(d.id)}
            >
              {d.skill_name}
              {building && d.id === activeTab ? (
                <span className="workspace-tab-meta"> · building…</span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}

      {activeGen ? (
        <GeneratedArtifactView skillId={activeGen} />
      ) : activeDerived ? (
        <DerivedView derived={activeDerived} building={building} />
      ) : (
        <ScratchpadPane />
      )}
    </div>
  );
}

function ProgressChip() {
  const state = useAgUiState<SignalState>();
  const processing = Boolean(state?.processing);
  const progress = state?.progress;
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (processing) {
      setVisible(true);
      return;
    }
    if (!progress) return;
    const timeout = setTimeout(() => setVisible(false), 1400);
    return () => clearTimeout(timeout);
  }, [processing, progress]);

  const isDev =
    process.env.NODE_ENV === "development" ||
    process.env.NEXT_PUBLIC_APP_ENV === "development";
  if (!isDev) return null;
  if (!visible || !progress) return null;

  const steps = progress.steps?.length ? progress.steps.join(" → ") : undefined;
  return (
    <div
      className={cn("progress-chip", !processing && "is-done")}
      title={steps}
      aria-live="polite"
    >
      <span className={processing ? "progress-chip-spinner" : "progress-chip-check"} />
      <span className="progress-chip-label">
        {processing ? progress.label ?? "Working" : "Done"}
      </span>
    </div>
  );
}

type CreditInfo = {
  enabled: boolean;
  enforced: boolean;
  balance: number | null;
  status: string;
};

/**
 * Current user's credit balance. Fetched on mount and again after every turn
 * ends; a refused turn also carries `credits_balance` on its state snapshot,
 * which we fold in immediately.
 */
function useCredits(): CreditInfo {
  const state = useAgUiState<SignalState>();
  const [info, setInfo] = useState<CreditInfo>({
    enabled: false,
    enforced: false,
    balance: null,
    status: "active",
  });

  const refresh = useRef(() => {});
  refresh.current = () => {
    fetch("/api/account", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setInfo({
          enabled: Boolean(d.credits_enabled),
          enforced: Boolean(d.credits_enforced),
          balance: typeof d.credits_balance === "number" ? d.credits_balance : null,
          status: d.status ?? "active",
        });
      })
      .catch(() => {});
  };

  useEffect(() => {
    refresh.current();
  }, []);
  useAuiEvent("thread.runEnd", () => refresh.current());

  // A refused turn puts the live balance on the snapshot before the poll lands.
  const snap = state?.credits_balance;
  useEffect(() => {
    if (typeof snap === "number") {
      setInfo((prev) => ({ ...prev, enabled: true, balance: snap }));
    }
  }, [snap]);

  return info;
}

function CreditsChip() {
  const { enabled, balance, enforced } = useCredits();
  if (!enabled || balance === null) return null;
  const empty = balance <= 0;
  return (
    <span
      className={cn("chat-bar-credits", empty && "is-empty")}
      title={
        enforced
          ? "Credits remaining — 1 per message"
          : "Credits remaining (not enforced yet)"
      }
    >
      <span className="chat-bar-credits-spark" aria-hidden>
        ⚡
      </span>
      {balance}
      <span className="chat-bar-credits-unit"> credits</span>
    </span>
  );
}

function OutOfCreditsNotice() {
  const { enabled, enforced, balance } = useCredits();
  if (!enabled || !enforced || balance === null || balance > 0) return null;
  return (
    <div className="credits-notice" role="status">
      <strong>You&rsquo;re out of credits.</strong> New messages are paused. Ask
      the admin to top up your account to keep going.
    </div>
  );
}

function FollowUpWorking() {
  const state = useAgUiState<SignalState>();
  if (!state?.follow_up_pending) return null;
  return (
    <div className="follow-up-working" role="status" aria-live="polite">
      <span className="follow-up-working-dot" />
      Looking for follow-ups…
    </div>
  );
}

function NewThreadButton() {
  const aui = useAui();
  return (
    <button
      type="button"
      className="chat-bar-new-thread"
      onClick={() => aui.threads.switchToNewThread()}
    >
      <PlusIcon className="size-4" />
      New Thread
    </button>
  );
}

function EnvBadge() {
  if (process.env.NEXT_PUBLIC_APP_ENV !== "development") return null;
  return (
    <span className="chat-bar-dev" aria-label="Development environment">
      DEV
    </span>
  );
}

function UserChip() {
  const auth = useAuth();
  if (auth.status !== "authed") return null;
  const { user, authDisabled } = auth;
  const label = user.name || user.email || "Account";
  const initial = label.slice(0, 1).toUpperCase();
  return (
    <div className="chat-bar-user" title={user.email || label}>
      {user.picture ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="chat-bar-user-avatar" src={user.picture} alt="" />
      ) : (
        <span className="chat-bar-user-avatar chat-bar-user-avatar-fallback">
          {initial}
        </span>
      )}
      <span className="chat-bar-user-name">{label}</span>
      {!authDisabled ? (
        <a className="chat-bar-signout" href="/api/auth/logout">
          Sign out
        </a>
      ) : null}
    </div>
  );
}

/** Persistent bar across the top of the chat pane — never overlaps messages. */
function ChatBar() {
  return (
    <div className="chat-bar">
      <div className="chat-bar-left">
        <EnvBadge />
        <UserChip />
        <CreditsChip />
      </div>
      <div className="chat-bar-right">
        <ProgressChip />
        <NewThreadButton />
      </div>
    </div>
  );
}

export default function AppPage() {
  const aui = useAui();
  const config = AuiConfig({
    suggestions: Suggestions([
      {
        title: "Jot down an idea",
        label: "and start reworking it",
        prompt:
          "Jot this down: we're launching faster cold starts for our edge functions next week.",
      },
      {
        title: "Think through a rough idea",
        label: "with a few angles",
        prompt:
          "I have a rough idea for a calendar that auto-defends focus time. Help me think it through.",
      },
    ]),
    tools: Tools({ toolkit }),
  });

  return (
    <AuiProvider extends={aui} config={config}>
      <ArtifactsProvider>
        <ArtifactHistoryProvider>
          <main className="app-workspace">
            <section className="app-chat">
              <ChatBar />
              <OutOfCreditsNotice />
              <div className="app-chat-thread">
                <Thread />
                <FollowUpWorking />
              </div>
            </section>
            <WorkspacePanel />
          </main>
        </ArtifactHistoryProvider>
      </ArtifactsProvider>
    </AuiProvider>
  );
}
