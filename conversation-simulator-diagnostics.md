# Conversation Simulator Diagnostics

## Summary

The current Signal setup is valid at a basic level, but it is using
ConversationSimulator's least-controlled mode. The immediate problem is not
only Signal's response quality; the simulator is generating conversations that
do not necessarily follow the workflow described by the golden.

## What ConversationSimulator does

DeepEval repeatedly:

1. Generates a synthetic user message.
2. Sends it to `model_callback`.
3. Records the returned assistant `Turn`.
4. Decides whether to continue.
5. Repeats until a stopping condition is reached.

`max_user_simulations` counts user-assistant cycles, not individual messages.
Therefore, 10 simulations can produce up to 20 `Turn` objects.

A `ConversationalGolden` does not define an exact conversation. It provides:

- `scenario`;
- `persona`;
- `expected_outcome`.

The simulator invents the actual user messages. It may ask repeated questions,
change direction, or request information that the original scenario did not
explicitly prescribe.

## Current Signal setup

The callback declares:

```python
def signal_callback(input: str, turns: list[Turn], thread_id: str) -> Turn:
```

This matches the documented callback API. Signal uses `thread_id` to create the
conversation ID and user ID, so backend state is persisted across simulated
turns. Ignoring `turns` is also valid because Signal retrieves history from
local conversation files.

However, the current simulator does not provide:

- a `simulation_graph`;
- a custom `stopping_controller`;
- fixed user turns;
- a custom simulator prompt.

It therefore uses the default LLM-driven user behavior.

## Main weakness

The goal scenario expects the simulated user to:

- provide three facts;
- answer clarification questions;
- select tone and length;
- allow Signal to draft and validate a post.

The default simulator is not guaranteed to do any of these. Increasing the turn
limit only gives the simulator more opportunities to drift or repeat itself.

## Recommended simulator design

For the goal scenario, use a DeepEval `simulation_graph`. DeepEval recommends
this when the trajectory matters and deterministic ordering, retries, branching,
or terminal states are required.

The simulated user should follow this trajectory:

```text
initial_request
    ↓
provide_three_product_facts
    ↓
provide_optional_preferences
    ↓
confirm_final_post
    ↓
terminal
```

Example user turns:

```text
Create a LinkedIn post for the Fujifilm X100VI. Tell me what information
you need first.
```

```text
The product is a compact camera, it has a hybrid viewfinder, and it uses
a 23mmF2 lens.
```

```text
Use a professional tone. I have no word-count requirement.
```

```text
The facts are complete. Please draft and validate the LinkedIn post.
```

The terminal node should end the simulation after the final assistant reply.
This is more reliable than expecting the default expected-outcome controller to
recognize completion.

## Stopping controller

A custom `stopping_controller` can stop when the assistant visibly reports
completion, such as when the last response confirms that:

- required facts were collected;
- preferences were applied;
- the draft was validated.

It can also stop after repeated failure responses so that the test does not
produce ten identical clarification loops.

This controller should inspect observable assistant content rather than private
Python state.

## Golden changes

The goal persona should explicitly describe cooperative behavior:

```text
The user cooperates with Signal. When asked for product facts, they provide
at least three concrete facts. When asked for preferences, they answer
directly. They do not ask Signal to research or verify product specifications.
```

The scenario should state that tone and word count are optional unless the user
requests them.

## Evaluation strategy

Use two separate test types.

### Controlled goal test

Use a simulation graph or deterministic `ConversationalTestCase` with:

- known user turns;
- a fixed expected workflow;
- a terminal success state;
- `GoalAccuracyMetric`.

This evaluates whether Signal completes the intended task.

### Exploratory simulator test

Keep the default LLM-driven simulator as a separate resilience test. It can
explore:

- repeated questions;
- vague descriptions;
- requests for external product facts;
- validation follow-ups;
- user corrections.

Failures from this test should be reviewed as robustness findings rather than
treated as definitive goal-accuracy regressions.

## Recommended implementation order

1. Add a simulation graph specifically for `goal_accuracy`.
2. Give it fixed user actions for the initial request, three facts, optional
   preferences, and final confirmation.
3. Add a terminal node after the final assistant reply.
4. Add a stopping controller for repeated failure responses.
5. Keep the current local transcript logging.
6. Run `GoalAccuracyMetric` against the controlled conversation.
7. Run the default simulator separately for exploratory behavior.

The key correction is not increasing the turn limit. The issue is that the
default simulator generates an unconstrained user conversation, while the
metric expects a specific cooperative workflow.
