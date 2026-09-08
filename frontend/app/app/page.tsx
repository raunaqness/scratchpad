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
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type ChoiceOption = {
  id: string;
  label: string;
};

type ChoiceArgs = {
  id?: string;
  question?: string;
  options?: ChoiceOption[];
};

type ChoiceToolProps = {
  args: ChoiceArgs;
  result?: unknown;
};

type SignalProgress = {
  node?: string | null;
  label?: string;
  status?: string;
  steps?: string[];
  done?: boolean;
};

type VersionItem = {
  seq: number;
  kind?: string;
  format?: string;
  title?: string;
  topic?: string;
  body?: string;
  angles?: string[];
  outline?: string[];
  open_questions?: string[];
  created_at?: string;
};

type SignalArtifact = {
  processing?: boolean;
  progress?: SignalProgress;
  status?: string;
  kind?: "idea_board" | "outline" | "draft";
  title?: string;
  topic?: string;
  angles?: string[];
  outline?: string[];
  body?: string;
  open_questions?: string[];
  version?: number;
  versions?: VersionItem[];
  head?: number;
  // back-compat mirrors still emitted by the backend
  draft?: string;
  draft_version?: number;
};

/**
 * Agent-generated picker. The backend emits a `request_choice` tool call
 * (e.g. brainstorm angles); we render radio buttons and a selection is sent
 * back as a normal user turn — the interpreter turns it into `chosen_angle`.
 */
function ChoiceTool({ args, result }: ChoiceToolProps) {
  const aui = useAui();
  const [selected, setSelected] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const options = args.options ?? [];
  const done = result !== undefined || submitted;

  if (done) {
    const label =
      options.find((option) => option.id === selected)?.label ??
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
          const label = options.find((option) => option.id === selected)?.label;
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

const toolkit = defineToolkit({
  request_choice: {
    type: "backend",
    render: (props) => (
      <ChoiceTool args={props.args as ChoiceArgs} result={props.result} />
    ),
  },
  browser_alert: {
    description: "Display a native browser alert dialog to the user.",
    parameters: {
      type: "object",
      properties: {
        message: {
          type: "string",
          description: "Text to display inside the alert dialog.",
        },
      },
      required: ["message"],
    },
    execute: async ({ message }) => {
      alert(message);
      return { status: "shown" };
    },
    render: ({ args, result }) => (
      <div className="mt-3 w-full max-w-(--thread-max-width) rounded-lg border px-4 py-3 text-sm">
        <p className="text-muted-foreground font-semibold">browser_alert</p>
        <p className="mt-1">
          Requested alert with message:
          <span className="text-foreground ml-1 font-mono">
            {JSON.stringify(args.message)}
          </span>
        </p>
        {result?.status === "shown" && (
          <p className="text-foreground/70 mt-2 text-xs">
            Alert displayed in this tab.
          </p>
        )}
      </div>
    ),
  },
});

const KIND_TITLE: Record<string, string> = {
  idea_board: "Idea board",
  outline: "Outline",
  draft: "Draft",
};

const STATUS_LABEL: Record<string, string> = {
  exploring: "Exploring angles",
  drafting: "Outline ready",
  refining: "Draft in progress",
  stable: "Stable",
  empty: "No draft yet",
};

// A fixed, front-end-authored "something is happening" sequence. It is NOT
// tied to which graph node runs — it just advances on a timer while a turn is
// in flight and holds on the last phrase until the backend says it's done.
const RITUAL_PHRASES = [
  "Analyzing your message",
  "Thinking it through",
  "Working on the draft",
  "Reviewing",
  "Finishing up",
];

function ArtifactRitual() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    setIndex(0);
    const id = setInterval(() => {
      setIndex((prev) => Math.min(prev + 1, RITUAL_PHRASES.length - 1));
    }, 1400);
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
// version history (linear list + preview + branch-with-confirmation)
// ---------------------------------------------------------------------------

type HistoryValue = {
  count: number;
  versions: VersionItem[];
  previewSeq: number | null; // null = viewing the live/head version
  isPreviewing: boolean;
  armedBase: number | null; // confirmed branch base, awaiting the next message
  displaySeq: number;
  step: (delta: number) => void;
  goToLatest: () => void;
  openBranchModal: () => void;
};

const HistoryContext = createContext<HistoryValue | null>(null);

function useHistory(): HistoryValue {
  const ctx = useContext(HistoryContext);
  if (!ctx) throw new Error("useHistory used outside ArtifactHistoryProvider");
  return ctx;
}

function ArtifactHistoryProvider({ children }: { children: ReactNode }) {
  const aui = useAui();
  const state = useAgUiState<SignalArtifact>();
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

  // Any finished turn snaps the panel back to the latest version.
  useAuiEvent("thread.runEnd", () => {
    setPreviewSeq(null);
    clearBranch.current();
  });

  // Switching threads resets history navigation.
  useAuiEvent("threads.selectionChanged", () => {
    setPreviewSeq(null);
    clearBranch.current();
  });

  // Keep the preview pointer in range if the list shrank.
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
  const discarded =
    count - base === 1 ? `v${base + 1}` : `v${base + 1}–v${count}`;

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

function ArtifactPanel() {
  const state = useAgUiState<SignalArtifact>();
  const history = useHistory();
  const processing = Boolean(state?.processing);
  const previewing = history.isPreviewing;

  // While previewing a past version, everything is drawn from that snapshot.
  const preview = previewing
    ? history.versions[(history.previewSeq as number) - 1]
    : undefined;

  const body = preview
    ? preview.body ?? ""
    : state?.body ?? state?.draft ?? "";
  const angles = preview ? preview.angles ?? [] : state?.angles ?? [];
  const outline = preview ? preview.outline ?? [] : state?.outline ?? [];
  const openQuestions = preview
    ? preview.open_questions ?? []
    : state?.open_questions ?? [];
  const title = preview ? preview.title : state?.title;
  const kind =
    (preview?.kind as SignalArtifact["kind"]) ??
    state?.kind ??
    (body ? "draft" : outline.length ? "outline" : "idea_board");

  const isOpen = Boolean(
    processing || body || angles.length || outline.length || history.count,
  );
  if (!isOpen) {
    return null;
  }

  const hasContent = Boolean(body || outline.length || angles.length);
  const showRitual = processing && !previewing;

  return (
    <aside
      className={cn(
        "artifact-panel",
        showRitual && "is-loading",
        previewing && "is-previewing",
      )}
    >
      <div className="artifact-header">
        <h2>{title || KIND_TITLE[kind ?? "draft"] || "Draft"}</h2>
        <VersionNav />
      </div>

      <div
        className="artifact-progressbar"
        aria-hidden={!showRitual}
        data-active={showRitual}
      />

      {showRitual ? <ArtifactRitual /> : null}
      <PreviewBanner />

      <div className="artifact-body">
        {body ? (
          <div className="artifact-copy">
            {body}
            {showRitual ? <span className="artifact-caret" /> : null}
          </div>
        ) : outline.length ? (
          <ol className="artifact-outline">
            {outline.map((beat, index) => (
              <li key={`${beat}-${index}`}>{beat}</li>
            ))}
          </ol>
        ) : angles.length ? (
          <ul className="artifact-angles">
            {angles.map((angle, index) => (
              <li key={`${angle}-${index}`}>{angle}</li>
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
            <p>Your piece will take shape here.</p>
            <span>
              Share a product or an idea and Signal starts putting angles on the
              board.
            </span>
          </div>
        )}

        {openQuestions.length ? (
          <div className="artifact-questions">
            <p className="artifact-questions-heading">Open questions</p>
            <ul>
              {openQuestions.map((question, index) => (
                <li key={`${question}-${index}`}>{question}</li>
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
                ? STATUS_LABEL[state?.status ?? ""] ?? "Draft in progress"
                : "No draft yet"}
        </span>
        <CopyButton text={body} />
      </div>
    </aside>
  );
}

function ProgressChip() {
  const state = useAgUiState<SignalArtifact>();
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

  // dev-only: true under `next dev`, false in a production build
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

function NewThreadButton() {
  const aui = useAui();

  return (
    <button
      type="button"
      onClick={() => aui.threads.switchToNewThread()}
      className="bg-background hover:bg-accent flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium shadow-sm transition-colors"
    >
      <PlusIcon className="size-4" />
      New Thread
    </button>
  );
}

function TopRightControls() {
  return (
    <div className="absolute top-4 right-4 z-10 flex items-center gap-2">
      <ProgressChip />
      <NewThreadButton />
    </div>
  );
}

function EnvironmentBadge() {
  if (process.env.NEXT_PUBLIC_APP_ENV !== "development") {
    return null;
  }

  return (
    <div className="environment-badge" aria-label="Development environment">
      DEV
    </div>
  );
}

export default function AppPage() {
  const aui = useAui();
  const config = AuiConfig({
    suggestions: Suggestions([
      {
        title: "Draft a LinkedIn post",
        label: "from three product facts",
        prompt:
          "Write a LinkedIn post for Fujifilm X100VI. Facts: compact body, 40.2MP sensor, hybrid viewfinder.",
      },
      {
        title: "Learn what Signal needs",
        label: "before drafting",
        prompt: "What information do you need to create my LinkedIn post?",
      },
    ]),
    tools: Tools({ toolkit }),
  });

  return (
    <AuiProvider extends={aui} config={config}>
      <ArtifactHistoryProvider>
        <main className="app-workspace">
          <section className="app-chat">
            <EnvironmentBadge />
            <TopRightControls />
            <Thread />
          </section>
          <ArtifactPanel />
        </main>
      </ArtifactHistoryProvider>
    </AuiProvider>
  );
}
