"use client";

import { useEffect, useState } from "react";
import {
  AuiConfig,
  AuiProvider,
  Suggestions,
  Tools,
  defineToolkit,
  useAui,
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
      <ChoiceTool
        args={props.args as ChoiceArgs}
        result={props.result}
      />
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

function ArtifactPanel() {
  const state = useAgUiState<SignalArtifact>();
  const processing = Boolean(state?.processing);
  const body = state?.body ?? state?.draft ?? "";
  const angles = state?.angles ?? [];
  const outline = state?.outline ?? [];
  const openQuestions = state?.open_questions ?? [];
  const version = state?.version ?? state?.draft_version ?? 0;
  const kind = state?.kind ?? (body ? "draft" : outline.length ? "outline" : "idea_board");

  const isOpen = Boolean(
    processing || body || angles.length || outline.length || version,
  );
  if (!isOpen) {
    return null;
  }

  const hasContent = Boolean(body || outline.length || angles.length);

  return (
    <aside className={cn("artifact-panel", processing && "is-loading")}>
      <div className="artifact-header">
        <div>
          <p className="artifact-kicker">Live artifact</p>
          <h2>{state?.title || KIND_TITLE[kind] || "Draft"}</h2>
        </div>
        {version ? <span className="artifact-version">v{version}</span> : null}
      </div>

      <div className="artifact-progressbar" aria-hidden={!processing} data-active={processing} />

      <div className="artifact-body">
        {body ? (
          <div className="artifact-copy">
            {body}
            {processing ? <span className="artifact-caret" /> : null}
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
        ) : processing ? (
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
          {processing
            ? state?.progress?.label
              ? `${state.progress.label}…`
              : "Working…"
            : hasContent
              ? STATUS_LABEL[state?.status ?? ""] ?? "Draft in progress"
              : "No draft yet"}
        </span>
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
      <main className="app-workspace">
        <section className="app-chat">
          <EnvironmentBadge />
          <TopRightControls />
          <Thread />
        </section>
        <ArtifactPanel />
      </main>
    </AuiProvider>
  );
}
