# Signal DeepEval Failure Report

## Run context

This report describes the latest per-scenario DeepEval run using the
ConversationSimulator and the configured OpenRouter chat model.

- Test command: `pytest -q -s tests/test_conversations.py --maxfail=7`
- Per-scenario turn budgets were read from `scenario.json`.
- DeepEval threshold: `0.7`
- One primary metric was evaluated for each scenario.
- The application runtime is traced in LangSmith.
- DeepEval is responsible for simulation and evaluation.

The run completed with four failing scenarios. OpenRouter was available and
the failures were evaluation or application-behavior failures, not provider
rate-limit failures.

## 1. Conversation completeness

### Scenario

`conversation_completeness`

The campaign manager provides a LinkedIn-post request for the Fujifilm X100VI
with audience, tone, maximum length, call to action, and product facts.

### Metric result

- Metric: `ConversationCompletenessMetric`
- Score: `0.0`
- Threshold: `0.7`

### Observed failure

DeepEval reported that Signal asked questions such as:

> What does the product do?

The evaluator expected Signal to produce the LinkedIn post because the
simulated user message already contained the required information.

### Likely cause

Signal relies too heavily on a single LLM extraction step to determine whether
fields are present. The extraction did not consistently recognize all product
facts or constraints in the simulator-generated wording. Signal then treated
the request as incomplete and asked for clarification.

The simulator can also phrase the request differently from the wording in the
golden, so the application must handle equivalent user language rather than
expect exact phrases.

### Possible solution

1. Make the extraction result a validated structured object with explicit
   fields for company, product, description, audience, tone, length, and call
   to action.
2. Merge newly extracted fields with previously confirmed conversation state.
3. Run a deterministic missing-field check after extraction.
4. Ask only for fields that remain absent.
5. Add a regression test where all fields are supplied in one user turn and
   assert that no clarification is requested.

## 2. Turn relevancy

### Scenario

`turn_relevancy`

The user provides the company and Fujifilm X100VI product name and asks for a
LinkedIn post, while omitting only the product description and length.

### Metric result

- Metric: `TurnRelevancyMetric`
- Score: `0.0`
- Threshold: `0.7`

### Observed failure

Signal asked for product details and tone. DeepEval considered that response
irrelevant because the simulator-generated user message had already supplied
or implied enough context, including the tone.

### Likely cause

There are two issues:

- The extractor does not reliably interpret phrases such as “engaging” or
  “professional” as an explicit tone preference.
- The clarification logic does not reliably distinguish missing fields from
  fields already confirmed in earlier turns.

The current required-field policy may also be too broad for this scenario if
company or tone are not actually missing.

### Possible solution

1. Normalize common preference language into structured values.
2. Preserve confirmed values across all turns.
3. Track the source and confirmation status of each field.
4. Generate clarification questions only for the exact missing fields.
5. Add a deterministic test asserting that Signal does not ask again for
   company, product, or tone once confirmed.

## 3. Unsupported claim prevention

### Scenario

`unsupported_claim_prevention`

The user provides factual X100VI details such as five-axis IBIS, up to 6.0
stops of compensation, a tilting LCD, a hybrid viewfinder, a 23mmF2 lens, and
a 40.2-megapixel sensor.

### Metric result

- Metric: custom `ConversationalGEval`
- Score: `0.0`
- Threshold: `0.7`

### Observed failure

The simulator generated a request asking Signal to verify or clarify product
specifications instead of directly asking for a LinkedIn post. Signal
redirected to its supported LinkedIn-post capability. The grounding evaluator
then judged that response as failing because it did not produce a post.

### Likely cause

This is primarily a scenario-to-metric mismatch rather than a clear
unsupported-claim failure. The golden describes the facts but does not state
strongly enough that the user wants a LinkedIn post using those facts.

A grounding metric cannot fairly evaluate a post when the simulated user asks
for product verification instead.

### Possible solution

1. Change the golden to explicitly request:
   “Write a LinkedIn post using only the following product facts.”
2. Keep product-specification verification as a separate out-of-scope
   scenario.
3. Use a fixed deterministic conversation for this exact X100VI fact set.
4. Keep the custom G-Eval focused only on unsupported claims in the generated
   post.
5. Add an independent assertion that every factual claim in the post appears
   in the supplied facts.

## 4. Goal accuracy

### Scenario

`goal_accuracy`

Signal is expected to collect missing product information, confirm tone and
length, create the LinkedIn post, validate it, and then return the final
result.

### Metric result

- Metric: `GoalAccuracyMetric`
- Score: `0.25`
- Threshold: `0.7`

### Observed failure

DeepEval reported that Signal did not provide the requested post or a
coherent plan for gathering the required information. The workflow did not
reach a complete goal state during the simulated conversation.

### Likely cause

The scenario is inherently multi-turn, but simulator behavior is
nondeterministic. A turn budget is only a maximum; it does not guarantee that
the simulator will answer each clarification in the intended way.

There is also a metric-boundary issue. Signal records an internal
`trajectory`, but `GoalAccuracyMetric` primarily evaluates the conversational
test case. Internal task state is not automatically equivalent to an
assistant-visible plan.

### Possible solution

1. Use a five-turn budget for this scenario, as currently configured.
2. Make the golden persona explicitly answer clarification questions.
3. Ensure every clarification is precise and easy for the simulator to
   answer.
4. Expose a short, user-safe task summary when appropriate, without exposing
   hidden chain-of-thought.
5. Add deterministic assertions over the stored trajectory:
   - facts collected;
   - preferences confirmed;
   - post generated;
   - constraints validated;
   - final response returned.
6. Consider using a custom conversational G-Eval for plan adherence, while
   reserving `GoalAccuracyMetric` for a stable end-to-end conversation.

## Cross-cutting findings

### ConversationSimulator is not deterministic input

A `ConversationalGolden` describes a situation, persona, and expected outcome.
It does not prescribe exact user messages. The simulator may omit information,
rephrase it, ask an unexpected question, or change the direction of the
conversation.

Therefore, a failing metric can mean either:

- Signal handled the generated conversation incorrectly; or
- the generated conversation no longer matches the behavior the metric was
  intended to measure.

Both cases must be visible in the report before changing application code.

### Recommended test layers

Use two complementary layers:

1. **ConversationSimulator tests**
   - broad behavioral coverage;
   - varied user phrasing and turn sequences;
   - resilience and role-boundary testing.

2. **Deterministic conversational tests**
   - exact X100VI multi-turn conversation;
   - exact clarification followed by product details;
   - exact tone, length, and unsupported-claim constraints;
   - stable regression checks for known bugs.

ConversationSimulator should not be the only test mechanism for a precise
acceptance case.

### Recommended fix order

1. Improve structured extraction and field-state merging.
2. Make clarification questions field-specific and non-repetitive.
3. Correct the grounding golden so it explicitly requests a LinkedIn post.
4. Add deterministic multi-turn tests for the X100VI conversation.
5. Validate the stored trajectory independently of DeepEval's conversational
   score.
6. Re-run the simulator suite after the deterministic regression tests pass.
