# CS 4603 — Agentic AI & LLMOps
## In-Class Project Sample: *Building "Atlas" — An LLM Assistant, One Decision at a Time*

---

### How this works

We follow one story: a small team building an internal LLM assistant called **Atlas**. The story grows stage by stage. At each stage a new engineering pressure appears — exactly the kind you will implement in the real project.

- Work through the stages **in order**. Each stage **reuses answers and numbers from earlier stages** — write them
  down as you go and keep them handy.
- These are **paper-and-pencil** exercises: reason, calculate, and decide. No coding required here.
- Where you see a **Checkpoint**, pause and make sure your answer is solid before moving on — the next stage depends on it.
- The goal is to build the *intuition* you'll need before you touch the codebase. Model answers will be provided separately

### The setting

**Cargo-lane** is a logistics company. Their support engineers keep re-reading the same policy manuals and doing the
same small calculations. The team decides to build **Atlas**: a chat assistant that answers questions over company
documents and can do light arithmetic.

Throughout the case study, assume Atlas's primary model has these fixed properties:

| Property | Value |
|---|---|
| Model context limit | **8,192 tokens** |
| System prompt size | **500 tokens** (never removed) |
| Typical reply reserved | **600 tokens** |
| Budget warning threshold | **80%** of the context limit |

---

## Stage 1 — The first prototype: history & token budgeting

Atlas v0 keeps the whole conversation as a list of message dicts (`{"role": ..., "content": ...}`), with the system
prompt pinned at the front. After each API call it adds `prompt_tokens + completion_tokens` from the `usage` field to a
running total, and prints a warning when the total reaches 80% of the limit. When history gets too big it trims the
**oldest non-system** messages first.

Here is the first conversation. Each row is one `chat()` call and the usage it reported:

| Call | `prompt_tokens` | `completion_tokens` | Running total (fill in) |
|---|---|---|---|
| 1 | 520 | 180 | |
| 2 | 900 | 300 | |
| 3 | 1,600 | 500 | |
| 4 | 2,200 | 700 | |

**Work through:**
1. Fill in the running-total column (cumulative across the conversation).
2. What is the exact 80%-of-limit warning threshold in tokens?
3. At which call does the warning **first** fire? State the total and the percentage used at that point.
4. Atlas trims history from the oldest end but **never** the system prompt. Give the two reasons the system prompt must be protected from truncation.

> **Checkpoint 1 — carry forward:** Keep your warning threshold (in tokens) and your final running total. You'll reuse both.

---

## Stage 2 — Token pressure: how much text actually fits?

A support engineer pastes an entire **policy manual** into the chat. The team measures tokenisation on a sample:
a **1,050-character** English passage encodes to **280 tokens**.

The full policy manual is **30,000 characters** of similar prose.

**Work through:**
1. Compute the characters-per-token ratio from the sample. Show working.
2. Estimate the token count of the full 30,000-character manual using that ratio.
3. Using the fixed numbers from the setting (limit 8,192; system 500; reply 600), compute how many tokens remain for
   *context* in a single request. Does the whole manual fit? By how much does it overflow or spare?
4. It doesn't fit. Give **two** different engineering responses the team could take, and state what each one costs
   or gives up.

Now the team measures characters-per-token across content types with two encodings:

| Content type | `cl100k_base` | `o200k_base` |
|---|---|---|
| Prose English | 4.1 | 4.4 |
| Python code | 3.2 | 3.6 |
| JSON | 2.6 | 2.9 |
| LaTeX math | 2.1 | 2.3 |

5. Which content type is the **most** token-efficient and which is the **least**? What does this mean for a prompt that
   must embed a large block of LaTeX?

> **Checkpoint 2 — carry forward:** Keep your chars-per-token ratio and your "tokens remaining for context" number.

---

## Stage 3 — The long manual strikes back: lost in the middle

The team decides to stuff a large (but fitting) slice of the manual into context. To test recall, they hide a single
unique fact — a *needle* — inside a long, homogeneous body of records, and ask Atlas to retrieve it. They place the
needle at three positions and run **3 trials each**:

| Needle position | Correct recalls (out of 3) |
|---|---|
| Beginning (top 10%) | 3 / 3 |
| Middle (40–60%) | 1 / 3 |
| End (bottom 10%) | 3 / 3 |

**Work through:**
1. Describe the positional effect you see. Is it consistent across trials?
2. Why might a model recall middle-of-context facts worse? What does this suggest about how attention is spread over a
   long sequence?
3. Cargolane's policy manual keeps its *refund exceptions* in the middle sections. Describe the **silent** bug this could
   cause for a support engineer using Atlas, and state one concrete guideline for how the team should arrange context to
   reduce the risk.

> **Checkpoint 3 — carry forward:** Remember that "the fact is in the document" does **not** guarantee "the model will use it." This drives every later stage.

---

## Stage 4 — Which model? Latency vs quality vs cost

The team benchmarks two candidate models on the same prompt:

| Metric | Model S (small) | Model L (large) |
|---|---|---|
| Time to First Token (TTFT) | 0.3 s | 0.9 s |
| Total latency | 2.0 s | 6.0 s |
| Output tokens | 400 | 450 |
| Manual quality rating (1–5) | 3 | 4 |
| Per-token rate (cost proxy) | $0.50 / 1k tokens | $3.00 / 1k tokens |

**Work through:**
1. Compute the cost proxy (output tokens × rate) for each model. Show working.
2. Atlas streams its answers into a live chat box. Explain what **TTFT** captures that **total latency** does not, and
   why TTFT matters for that streaming UX.
3. Model L's TTFT is 3× worse and it costs far more, for only +1 quality. Give one Cargolane use case where you'd
   **still** pick Model L, and one where you'd **always** pick Model S regardless of quality.
4. Name two real pricing factors your cost proxy ignores that would change the true production cost.

> **Checkpoint 4 — carry forward:** Pick one model as Atlas's default and note *why*. Later stages assume that choice.

---

## Stage 5 — Making answers reliable: PTCF and measuring quality

Atlas must extract structured facts: given several **Record-IDs**, return each one's **Sector** exactly. The team builds
prompt variants using the **PTCF** framework (Persona, Task, Context, Format) and scores each with **exact-match F1**.

| Variant | F1 |
|---|---|
| Baseline (no PTCF) | 0.41 |
| Persona only | 0.44 |
| Task only | 0.63 |
| Context only | 0.58 |
| Format only | 0.71 |
| Full PTCF | 0.90 |

**Work through:**
1. Which **single** PTCF component gives the biggest jump over baseline? Report before, after, and the gain.
2. *Format only* beats *Task only*. Why is the **Format** pillar so powerful when the score is **exact string equality**?
3. On a fresh run the team logs one extraction: ground truth has **12** target entities; Atlas returns **10** entities,
   of which **8** exactly match after normalisation. Compute **precision**, **recall**, and **F1**. Show working.
4. Their evaluator uses exact string matching. Give one case where a *correct* answer is scored *wrong*, and state the
   tradeoff of switching to fuzzy matching.

> **Checkpoint 5 — carry forward:** Note your best prompt variant and your F1 formula — both return in the final stage.

---

## Stage 6 — Prompting strategies: zero-shot, few-shot, CoT

Using their best PTCF prompt as the base, the team tries three strategies on the same extraction task (which runs
against a large haystack that nearly fills the context):

| Strategy | Description |
|---|---|
| Zero-shot | Base prompt only |
| Few-shot (2-shot) | Base prompt + two labelled examples |
| Chain-of-Thought | Base prompt + "reason step by step, then give the final answer" |

**Work through:**
1. For a mechanical *exact-extraction* task, would you expect **CoT** to help a lot, a little, or possibly hurt? Explain.
2. Few-shot examples consume context that competes with the haystack. Explain the point at which **adding more examples
   starts to reduce** performance, and how you'd test that threshold.
3. Connect this back to Stage 3: how does packing few-shot examples into an already-full context interact with the
   *lost-in-the-middle* effect?

---

## Stage 7 — Giving Atlas tools: calculator + document lookup

Support engineers now ask things like: *"Find the standard restocking fee in the manual, then tell me the total for a
25% surcharge."* Atlas can't do this from the prompt alone, so the team adds two tools and a multi-turn loop
(`max_turns = 5`):

- **Calculator** — evaluates a math expression string (safe, four basic operations).
- **Document lookup** — takes a query, searches local snippets by keyword overlap, returns the best match or "not found".

**Work through:**
1. Explain the difference between **one API call that merely *has* tools available** and a **multi-turn loop** that runs
   tools and continues. Why does the surcharge question above *require* the loop?
2. Atlas returns **two tool calls at once** (a lookup and a calculation). When you send results back, what must be
   attached to each result so the model matches it to the right call?
3. If the document-lookup tool raises an exception, should the loop **stop**, **skip the tool**, or **pass the error back
   to the model**? Pick one and justify with a concrete Atlas example.
4. The loop hits `max_turns` without a final answer. Why return `"Task incomplete: maximum tool calls reached."`
   instead of raising a bare exception?

> **Checkpoint 7 — carry forward:** You now have a full Atlas: model choice + PTCF prompt + strategy + tool loop.

---

## Stage 8 — When Atlas breaks, and the final integration

**Part A — Failure modes.** In testing, three failures appear. For each, name *where* the fix belongs and *what* you'd change:

| Failure | What happens |
|---|---|
| Wrong tool choice | Atlas calls the calculator when it should have looked up a document |
| Loop pressure | Atlas keeps calling tools and never produces a final answer |
| Bad arguments | Atlas passes a malformed expression to the calculator |

1. For **bad arguments**, where should the validation happen: before the tool runs, inside the tool, or in a separate
   validation step? Justify.
2. Can **loop pressure** happen during a *normal* user request, not just a designed test? Which kinds of requests trigger it?

**Part B — The final experiment.** The team runs the *same* document-grounded query — *"Find the reported restocking
fee, then compute the total after a 25% surcharge"* — under four conditions:

| Condition | Configuration |
|---|---|
| 1. Baseline | Vague prompt, no tools, no injected context |
| 2. Prompt | Best PTCF prompt (Stage 5) |
| 3. Prompt + Strategy | Best PTCF prompt + best strategy (Stage 6) |
| 4. Prompt + Strategy + Tools | All of the above + document lookup via the tool loop (Stage 7) |

3. Which condition is the **minimum** that can answer this query *correctly and reliably*? Explain why conditions 1–2
   can fail even with a strong model — and tie your answer back to **Stage 3 (lost in the middle)** and
   **Stage 7 (the tool loop)**.
4. Give one Cargolane situation where the cheapest condition (1) is actually the *right* engineering choice, even though
   it scores lowest on quality.

> **Final checkpoint:** You've now reasoned through the entire arc of the real assignment — client, tokens, context,
> model choice, prompting, tools, and failure handling — on a single running example. 

---

