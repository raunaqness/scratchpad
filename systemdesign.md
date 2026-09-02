# Signal MVP System Design

## 1. Purpose and scope

Signal is an agentic AI content-marketing assistant for people who create
company content. The long-term product may support blogs, social media, and
LinkedIn content. The MVP supports one capability only:

> Help a user create a LinkedIn post promoting a product.

The MVP is intentionally small and backend-only:

- one backend conversation entry point; no frontend is part of this phase;
- one LangGraph graph;
- one agent operating in a reason-and-act (ReAct-style) loop;
- one capability agent for LinkedIn posts;
- DeepEval-driven multi-turn evaluation;
- local file persistence only;
- DeepEval for test cases, goldens, and metrics;
- LangSmith for runtime observability;
- OpenRouter as the only LLM gateway;
- no database, retrieval, authentication, or publishing integrations yet.

The system should not silently fill in missing information. It may suggest
options, but it must ask the user to choose before treating a preference as
specified.

## 2. Planned directory structure

Only this design document is created in the current phase. The planned
implementation should initially contain the fewest files needed:

```text
signal_v2/
├── app.py
├── config.py
├── prompts.py
├── guardrails.json
├── capabilities/
│   ├── __init__.py
│   └── social_media.py
├── data/
│   ├── conversations/
│   ├── logs/
│   └── memory/
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_conversations.py
    └── goldens.py
```

### `app.py`

The application entry point. It will:

- define the LangGraph state;
- receive the current user message and conversation history;
- extract facts and explicit preferences from the conversation;
- decide whether clarification is needed;
- create and execute a task plan;
- route work to the appropriate capability;
- validate that all planned tasks are complete;
- attach LangSmith metadata to the LangGraph invocation;
- load and update the user's local memory file;
- apply the guardrails before and during execution;
- return either one focused clarification or the completed response.

The graph should keep orchestration separate from content-writing behavior.
`app.py` should not contain detailed LinkedIn-copying instructions.

Every conversation request must have a stable `conversation_id` and a
`user_id`. The `conversation_id` identifies one chat thread and is used to
correlate turns, local conversation files, LangSmith traces, and DeepEval test
cases. A separate request/run identifier may identify one turn within that
conversation.

### `capabilities/social_media.py`

The only MVP capability agent. It receives a structured request containing
facts and user-selected constraints, then produces a LinkedIn post. It must
use only the supplied information and clearly mark any user-provided claim
that is insufficiently specific instead of inventing supporting details.

The capability should not ask follow-up questions itself. Missing required
information is handled by the main graph so that the conversation has one
consistent clarification behavior.

### `tests/`

The evaluation suite will contain multi-turn DeepEval cases and the initial
conversation goldens. The tests will call the application with a conversation
and evaluate the resulting `ConversationalTestCase`.

`goldens.py` will initially keep a small, reviewable set of scenarios close to
the tests. A separate persisted dataset is unnecessary until the first cases
and their schema have stabilized.

### `config.py`

The single configuration boundary. It should load environment variables (from
the shell or a local `.env` file later) and expose typed settings such as:

- `development_mode` / `debug`;
- OpenRouter API key, model, temperature, token limit, timeout, and retry
  settings;
- local data directory;
- LangSmith tracing flag, API key, endpoint, and project;
- prompt version;
- guardrails path.

Secrets must come from environment variables and must never be committed or
written to logs. Development mode may enable verbose application logs and
trace metadata; production mode should keep the same behavior with reduced
log detail. Configuration should fail clearly when a required production
setting is missing.

### OpenRouter-only model access

All LLM calls in the backend must use OpenRouter. The implementation should
use an OpenAI-compatible client integration configured with:

- base URL: `https://openrouter.ai/api/v1`;
- API key: `OPENROUTER_API_KEY`;
- model: `OPENROUTER_MODEL`;
- optional generation controls such as
  `OPENROUTER_TEMPERATURE` and `OPENROUTER_MAX_TOKENS`;
- timeout and retry settings from configuration.

There must be no provider-specific fallback, implicit local model, or direct
OpenAI/Anthropic/Gemini API path in the MVP. Changing models should require
changing configuration, not application logic. The selected OpenRouter model
and provider metadata should be recorded in local run metadata and safe
observability metadata, while the API key must never be recorded.

DeepEval is used for test cases, conversation goldens, simulation, and
evaluation metrics. LangSmith is used for runtime observability. Their
model-backed evaluators may use their separately configured evaluation
settings, but the Signal application runtime itself uses OpenRouter only.

### `prompts.py`

The central, versioned prompt module. Application code must import prompts
from here rather than embedding system prompts in graph or capability code.
It should expose named prompt versions, for example
`MARKETING_AGENT_V1`, and a selected `CURRENT_PROMPT_VERSION`.

The first system prompt must be represented exactly as the initial baseline:

> Hey, you are a helpful marketing agent. Your goal is to help the user with
> their tasks. Break down into tasks and use specific tools.

As the application becomes more constrained, the prompt version should also
state the LinkedIn-only MVP boundary and the no-unsupported-assumptions rule.
Prompt version, not prompt text alone, should be recorded in local metadata and
both observability systems.

### `guardrails.json`

The declarative allow/deny policy for the MVP. JSON is preferred initially so
the standard library can parse it without adding a YAML dependency. It should
define:

- the only allowed capability: create a LinkedIn post;
- allowed input fields and output constraints;
- requests that must be refused or redirected;
- the no-fabrication rule for facts and marketing claims;
- whether clarification is required for missing product information or
  unselected preferences.

Guardrails must be enforced in application code before capability execution
and again on the generated draft. The LLM prompt is guidance, not the
security boundary. A blocked request must not invoke the writing capability or
claim that an unsupported action happened.

### `data/`

All MVP persistence is local and file-based. This is a development
implementation boundary, not a production storage design:

- `data/conversations/<conversation_id>.json`: ordered turns, extracted
  state, task status, timestamps, prompt version, and final output;
- `data/memory/<user_id>.json`: durable user facts and explicit preferences;
- `data/logs/<date>.jsonl`: structured application and error events.

Writes should be atomic where practical, use UTF-8 JSON/JSONL, and avoid
secrets or unnecessary raw sensitive content. The data directory must be
excluded from version control when it can contain user data. File access must
be scoped to validated IDs to prevent path traversal.

## 3. Minimal state model

The LangGraph state should remain a small typed object or dictionary with
these fields:

- `turns`: ordered user and assistant messages;
- `company`: company explicitly supplied by the user, or unset;
- `product_name`: product explicitly supplied by the user, or unset;
- `product_description`: product information explicitly supplied by the
  user, or unset;
- `preferences`: explicitly selected tone, length, audience, call to action,
  and other supported constraints;
- `pending_questions`: focused questions required before work can continue;
- `tasks`: ordered tasks derived from the user's request;
- `completed_tasks`: tasks successfully completed;
- `draft`: the generated LinkedIn post, when available;
- `status`: one of `needs_clarification`, `in_progress`, `complete`, or
  `out_of_scope`.

Unset values must remain unset. The agent must distinguish between:

- a fact stated by the user;
- a preference selected by the user;
- an option suggested by Signal but not selected;
- an assumption, which is not allowed in the MVP.

## 4. Agent loop and control flow

The graph should execute the following cycle for each user turn:

1. **Understand** the latest request in the context of prior turns.
2. **Extract** only explicit company, product, description, requirements, and
   preferences.
3. **Check scope**. Continue only for LinkedIn-post creation; briefly explain
   the supported scope for other requests.
4. **Identify missing inputs**. If the request cannot be completed safely,
   ask one focused follow-up question. A clarification may offer choices such
   as tone or length, but must not select one for the user.
5. **Plan** the work as explicit tasks, for example:
   - confirm the product and company;
   - confirm or collect the product description;
   - confirm tone and length;
   - draft the post;
   - check the draft against the supplied facts and constraints.
6. **Act** by invoking the LinkedIn capability agent.
7. **Validate** that every task is complete and that the draft does not
   contain unsupported claims or unselected options.
8. **Respond** only after all tasks are complete. If not complete, continue
   the loop with a clarification or another internal task.

Conceptually:

```text
user turn
   ↓
extract explicit information
   ↓
scope check ── out of scope ──> explain MVP boundary
   ↓
missing required information? ── yes ──> ask one clarification
   ↓ no
create task plan
   ↓
run LinkedIn capability
   ↓
validate facts, preferences, and task completion
   ├── incomplete ──> continue loop
   └── complete ──> return final post
```

The loop is an orchestration loop, not a requirement to expose private chain
of thought. Later implementation should record observable state transitions,
task decisions, capability calls, and validation results without returning
hidden reasoning to the user.

## 5. MVP interaction contract

### Supported request

For a request such as:

> Write a LinkedIn post launching the Fujifilm X100. Fujifilm is the
> company. It is a compact camera for street photography. Make it confident
> and keep it under 100 words.

Signal should extract:

- company: Fujifilm;
- product: Fujifilm X100;
- description: compact camera for street photography;
- tone: confident;
- length: under 100 words;
- requested output: one LinkedIn post.

It may use only those facts. It must not invent a price, launch date,
specifications, availability, performance claims, or hashtags presented as
facts.

### Missing preferences

If the user supplies the product but not tone or length, Signal should ask
for those preferences or present a small set of choices. For example:

> What tone would you like: professional, confident, conversational, or
> playful? Also, should the post be short, medium, or long?

The choice must be confirmed by the user before it becomes part of the
generation request. If the user explicitly says “choose for me,” that is an
explicit instruction and can be recorded as such.

### Unsupported requests

The MVP should decline or redirect requests to write blogs, email campaigns,
non-LinkedIn platform content, publish content, browse for product facts, or
perform unrelated general-purpose tasks. It should explain that the current
capability is limited to creating LinkedIn posts from user-provided
information.

## 6. DeepEval-first test design

The test suite should use `ConversationalTestCase` and `Turn`, because the
important behavior spans multiple turns. Each golden should define:

- `scenario`: the user situation and relevant persona;
- `expected_outcome`: the observable successful result;
- persona or role guidance for the assistant;
- the conversation turns used for evaluation.

The initial tests should be small, deterministic in their input, and
behavior-focused. Exact wording should not be asserted unless the requirement
is truly textual; evaluation should primarily check facts, scope, completion,
and constraints.

### Initial conversation goldens

#### Golden 1: complete LinkedIn request

- **Scenario:** A Fujifilm marketer wants to launch the Fujifilm X100.
- **Expected outcome:** Signal returns one LinkedIn post using the supplied
  product description, confident tone, and requested short length.
- **Expected behavior:** No clarification is required and no unsupported
  product claims are introduced.
- **Primary metrics:** conversation completeness, turn relevancy, role
  adherence, goal accuracy.

#### Golden 2: clarification before generation

- **Scenario:** A user asks for a LinkedIn post for a named product but gives
  no product description, tone, or length.
- **Expected outcome:** Signal asks a focused clarification and does not
  generate a factual product launch post yet.
- **Expected behavior:** It offers choices without selecting a tone or length
  on the user's behalf.
- **Primary metrics:** turn relevancy, role adherence, conversation
  completeness.

#### Golden 3: retention across turns

- **Scenario:** The user gives the company, product name, and description in
  separate turns, then selects a professional tone and medium length.
- **Expected outcome:** The final post retains all earlier facts and the
  selected preferences.
- **Expected behavior:** No earlier fact is dropped, replaced, or contradicted.
- **Primary metrics:** knowledge retention, turn relevancy, conversation
  completeness, goal accuracy.

#### Golden 4: out-of-scope request

- **Scenario:** The user asks Signal to write a blog post, publish to
  LinkedIn, or answer an unrelated question.
- **Expected outcome:** Signal clearly redirects to the supported LinkedIn
  post-writing capability and does not pretend to have performed the
  unsupported action.
- **Primary metrics:** role adherence, turn relevancy, conversation
  completeness.

#### Golden 5: multiple requested constraints

- **Scenario:** The user requests a LinkedIn post with a specific audience,
  tone, maximum length, call to action, and product facts.
- **Expected outcome:** Signal completes every requested constraint before
  returning the final post.
- **Expected behavior:** It asks for any missing required choice and returns
  no partial final answer while required tasks remain incomplete.
- **Primary metrics:** conversation completeness, goal accuracy, turn
  relevancy.

#### Golden 6: no unsupported assumptions

- **Scenario:** The user gives only a product name and a brief description.
- **Expected outcome:** The result contains no invented price, date,
  specification, availability, testimonial, or performance claim.
- **Expected behavior:** Missing information is either omitted or requested;
  it is never silently fabricated.
- **Primary metrics:** knowledge retention, role adherence, conversation
  completeness. A future custom `ConversationalGEval` can directly score
  unsupported-claim avoidance.

#### Golden 7: observable plan and capability trajectory

- **Scenario:** A valid request requires clarification followed by
  generation.
- **Expected outcome:** The recorded trajectory shows the clarification,
  task planning, capability invocation, and completion validation in order.
- **Primary metrics:** goal accuracy and tool use, when the trajectory
  contains the corresponding observable fields.

## 7. Requested metrics and practical MVP configuration

The initial metric set is:

- `ConversationCompletenessMetric`: every user intention is addressed;
- `TurnRelevancyMetric`: each assistant turn stays relevant to the
  conversation;
- `KnowledgeRetentionMetric`: earlier company, product, description, and
  preferences are retained;
- `RoleAdherenceMetric`: the assistant remains a constrained LinkedIn content
  assistant;
- `GoalAccuracyMetric`: the plan and execution reach the requested result;
- `ToolUseMetric`: tool selection and arguments are correct when tools exist.

`RoleAdherenceMetric` requires `chatbot_role` on the conversational test
case. The role should be phrased narrowly, for example:

> You are Signal, an assistant that helps users create LinkedIn marketing
> posts from user-provided product information. You do not write other
> content types, publish content, browse for facts, or invent claims.

The first implementation should use a common starting threshold such as
`0.7`, then raise thresholds after observing stable evaluation behavior.
Thresholds are quality gates, not substitutes for scenario-specific assertions
about facts and constraints.

`GoalAccuracyMetric` and `ToolUseMetric` evaluate agentic trajectory data and
require `tools_called` when that field is used. The MVP has no external tool,
so it must not fabricate tool calls merely to satisfy a metric. Initially:

- record the internal plan and capability invocation as observable
  application events;
- use `GoalAccuracyMetric` only if the DeepEval version can evaluate that
  trajectory without pretending the capability is an external tool;
- keep `ToolUseMetric` as a reserved metric/configuration for the first real
  tool, or add it only to a test with an explicitly modeled capability tool.

The absence of external tools is an intentional MVP limitation, not a
successful tool-use result.

## 8. Minimal evaluation sketch

The later test implementation should follow the local DeepEval API shape:

```python
from deepeval import evaluate
from deepeval.metrics import (
    ConversationCompletenessMetric,
    GoalAccuracyMetric,
    KnowledgeRetentionMetric,
    RoleAdherenceMetric,
    ToolUseMetric,
    TurnRelevancyMetric,
)
from deepeval.test_case import ConversationalTestCase, Turn

test_case = ConversationalTestCase(
    scenario="A Fujifilm marketer launches the Fujifilm X100.",
    expected_outcome="A factually grounded LinkedIn post is completed.",
    chatbot_role=(
        "A constrained LinkedIn marketing assistant that uses only "
        "user-provided product information."
    ),
    turns=[
        Turn(role="user", content="..."),
        Turn(role="assistant", content="..."),
    ],
)

evaluate(
    test_cases=[test_case],
    metrics=[
        ConversationCompletenessMetric(threshold=0.7),
        TurnRelevancyMetric(threshold=0.7),
        KnowledgeRetentionMetric(threshold=0.7),
        RoleAdherenceMetric(threshold=0.7),
        GoalAccuracyMetric(threshold=0.7),
    ],
)
```

The ellipses above are placeholders for the generated application's actual
turns. The initial cases can use hand-authored expected conversations to
define the behavior, then the application output will replace the assistant
turns as implementation progresses. `ToolUseMetric` should be enabled only
when the test case includes a real, explicitly modeled tool trajectory and
the required `ToolCall` fields.

The first coding phase after this document should therefore implement the
graph contract and one capability against these cases, while keeping the
goldens and metric configuration close enough to the application that every
behavioral change can be evaluated immediately.

## 9. Conversation tracing and observability

Signal uses separate systems for runtime observability and evaluation:

- LangSmith traces the runtime LangGraph execution end to end;
- DeepEval defines goldens, simulates conversations, and evaluates test cases.

These integrations should be additive. Neither dashboard is the source of
truth for application state; local conversation and memory files remain the
MVP source of truth.

### Correlation model

For every request:

1. Generate or accept a validated `user_id`.
2. Generate or accept a stable `conversation_id` for the chat thread.
3. Generate a per-turn `request_id`.
4. Reuse the `conversation_id` as the thread correlation value in LangSmith.
5. Include only safe correlation metadata: feature name, environment,
   application version, prompt version, graph route, and outcome.

One conversation may contain multiple end-to-end runs/traces, one per user
turn. The shared conversation/session ID groups those traces. The request ID
identifies one run, while nested spans identify graph nodes and model or
capability calls.

### LangSmith runtime tracing

LangSmith's native LangChain/LangGraph tracing should be the only runtime
tracing mechanism. Enable it with `LANGSMITH_TRACING=true` and
`LANGSMITH_API_KEY`, and select the destination with `LANGSMITH_PROJECT` and,
when needed, `LANGSMITH_ENDPOINT`. LangChain and LangGraph invocations are
then traced automatically through their callback system.

The resulting trace should expose the complete ordered trajectory:

```text
LangSmith trace: one graph invocation
├── agent: graph run
├── node: information extraction / routing
│   └── llm: structured extraction
├── node: planning
│   └── llm: task plan
├── node: capability
│   └── llm: LinkedIn draft
└── node: validation / response
    └── llm: final validation, if needed
```

Pass trace metadata and tags on the `RunnableConfig` for each graph invocation.
At minimum, include `conversation_id` (as both `session_id` and `thread_id`),
`user_id`, application environment, feature, and prompt version. LangSmith
threads use `session_id` or `thread_id` metadata, and the values must be
inherited by child runs.

Trace payloads must exclude API keys, credentials, and raw sensitive user data
unless an approved masking policy is added. If tracing is disabled or
credentials are unavailable in development, the
application should continue locally with a clear warning. In production,
configuration should make the intended tracing policy explicit and report
startup errors for required observability settings.

### What is and is not traced

Trace the AI execution path and useful diagnostics:

- graph invocation and node transitions;
- LLM inputs/outputs after redaction;
- selected prompt version and model;
- task plan and completion status;
- capability invocation and validation result;
- guardrail decision and failure category;
- latency and errors;
- `user_id`, `conversation_id`, and `request_id` as correlation metadata.

Do not trace:

- API keys, access tokens, or credentials;
- full memory files by default;
- unnecessary personal information;
- hidden chain-of-thought or private reasoning;
- duplicate copies of the same LLM call.

LangSmith must be treated as best-effort telemetry. A telemetry outage must
not corrupt the local conversation or memory write, and an application failure
must still be logged locally.

## 10. User memory

Memory begins as one JSON file per user:

```json
{
  "user_id": "user-123",
  "facts": [
    {
      "key": "job_role",
      "value": "marketing professional",
      "source": "user",
      "confidence": "explicit",
      "updated_at": "2026-09-02T00:00:00Z"
    },
    {
      "key": "company",
      "value": "Fujifilm",
      "source": "user",
      "confidence": "explicit",
      "updated_at": "2026-09-02T00:00:00Z"
    }
  ],
  "preferences": {}
}
```

Only explicit, durable user facts should be saved. A product detail supplied
for one campaign should remain conversation state unless the user indicates
that it is a durable fact or preference. The memory process should:

1. load `data/memory/<user_id>.json` at the start of a request;
2. use it as context, without treating uncertain or inferred values as
   confirmed;
3. extract newly stated facts;
4. resolve conflicts by retaining provenance and latest explicit value;
5. write the updated file atomically after successful extraction;
6. never store model guesses as facts.

Memory must be scoped by `user_id`, validated before constructing a path, and
excluded from source control. The MVP does not need semantic search or a
vector database.

## 11. Implementation sequence

The next implementation phase should proceed in this order:

1. Add `config.py`, `prompts.py`, `guardrails.json`, and local data-directory
   helpers.
2. Add the minimal LangGraph graph and LinkedIn capability.
3. Add conversation and user-memory JSON persistence.
4. Enable LangSmith's LangChain/LangGraph tracing and verify one trace in the
   LangSmith project.
5. Add DeepEval's simulator and metric evaluation against the backend output.
6. Add the DeepEval pytest structure and run the initial conversation
   goldens.

The application must remain usable if either remote tracing system is
disabled. Local files and the DeepEval test suite are the development
baseline; remote dashboards are observability destinations.

## 12. Backend-only boundary

There is no frontend, browser client, or UI state in the MVP. The backend
should expose a small callable entry point first, such as:

```python
response = run_conversation(
    user_id="user-123",
    conversation_id="conversation-123",
    user_message="Write a LinkedIn post for the Fujifilm X100.",
)
```

The entry point returns a structured result containing the assistant message,
conversation ID, status, pending questions, and safe run metadata. A future
HTTP or frontend layer may call this function without changing the graph,
capability, persistence, guardrails, prompts, or evaluation contract.

DeepEval tests should exercise this backend entry point directly. They should
load the configured OpenRouter model through the application configuration,
while unit-level tests may inject a deterministic fake model where live LLM
calls would make a test slow or nondeterministic. No frontend-specific
dependencies should be added until the backend behavior passes the initial
evaluation suite.
