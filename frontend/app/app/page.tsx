"use client";

import { useState } from "react";
import {
  AuiConfig,
  AuiProvider,
  Suggestions,
  Tools,
  defineToolkit,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import { Check, PlusIcon } from "lucide-react";
import {
  useAgUiInterrupts,
  useAgUiState,
  useAgUiSubmitInterruptResponses,
} from "@assistant-ui/react-ag-ui";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";

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
  interrupt?: unknown;
  result?: unknown;
};

type SignalArtifact = {
  processing?: boolean;
  progress?: {
    stage?: string;
    label?: string;
    completed_steps?: string[];
  };
  status?: string;
  draft?: string;
  draft_version?: number;
  validation?: {
    status?: string;
  };
  requirements?: {
    required?: {
      product_name?: boolean;
      product_fact_count?: number;
      minimum_product_facts?: number;
    };
  };
};

function ChoiceTool({ args, interrupt, result }: ChoiceToolProps) {
  const [selected, setSelected] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submitInterruptResponses = useAgUiSubmitInterruptResponses();
  const pendingInterrupts = useAgUiInterrupts();
  const options = args.options ?? [];
  const hasResult = result !== undefined;
  const pendingInterrupt = pendingInterrupts.find(
    (item) =>
      item.id === args.id ||
      item.toolCallId === args.id ||
      item.toolCallId === `choice-${args.id}`,
  );

  if (hasResult && result !== undefined) {
    return (
      <div className="choice-card choice-card-complete">
        <Check size={16} />
        <span>Selected: {String(result)}</span>
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
        disabled={!selected || !pendingInterrupt || submitting}
        onClick={async () => {
          if (!pendingInterrupt || !selected) return;
          setSubmitting(true);
          try {
            await submitInterruptResponses([
              {
                interruptId: pendingInterrupt.id,
                status: "resolved",
                payload: { id: args.id, value: selected },
              },
            ]);
          } finally {
            setSubmitting(false);
          }
        }}
      >
        {submitting ? "Proceeding…" : "Proceed"}
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
        interrupt={props.interrupt}
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

function ArtifactPanel() {
  const artifact = useAgUiState<SignalArtifact>();
  const draft = artifact?.draft ?? "";
  const requirements = artifact?.requirements?.required;
  const isReady = Boolean(draft);
  const isOpen = Boolean(artifact?.processing || draft || artifact?.draft_version);

  if (!isOpen) {
    return null;
  }

  return (
    <aside className="artifact-panel">
      <div className="artifact-header">
        <div>
          <p className="artifact-kicker">Live artifact</p>
          <h2>LinkedIn draft</h2>
        </div>
        {artifact?.draft_version ? (
          <span className="artifact-version">v{artifact.draft_version}</span>
        ) : null}
      </div>
      <div className="artifact-body">
        {artifact?.progress ? (
          <details className="debug-progress" open={artifact.processing}>
            <summary>Backend progress</summary>
            <p>{artifact.progress.label}</p>
            {artifact.progress.completed_steps?.length ? (
              <ol>
                {artifact.progress.completed_steps.map((step, index) => (
                  <li key={`${step}-${index}`}>{step}</li>
                ))}
              </ol>
            ) : (
              <span>Current stage: {artifact.progress.stage}</span>
            )}
          </details>
        ) : null}
        {isReady ? (
          <div className="artifact-copy">{draft}</div>
        ) : (
          <div className="artifact-empty">
            <div className="artifact-empty-mark">S</div>
            <p>Your draft will take shape here.</p>
            <span>
              Share a product name and at least three concrete facts to begin.
            </span>
          </div>
        )}
      </div>
      <div className="artifact-footer">
        <span>
          {artifact?.validation?.status === "passed"
            ? "Validated"
            : artifact?.status === "needs_clarification"
              ? "Waiting for your input"
              : isReady
                ? "Draft in progress"
                : requirements?.product_fact_count
                  ? `${requirements.product_fact_count} facts collected`
                  : "No draft yet"}
        </span>
      </div>
    </aside>
  );
}

function NewThreadButton() {
  const aui = useAui();

  return (
    <button
      type="button"
      onClick={() => aui.threads.switchToNewThread()}
      className="bg-background hover:bg-accent absolute top-4 right-4 z-10 flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium shadow-sm transition-colors"
    >
      <PlusIcon className="size-4" />
      New Thread
    </button>
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
          <NewThreadButton />
          <Thread />
        </section>
        <ArtifactPanel />
      </main>
    </AuiProvider>
  );
}
