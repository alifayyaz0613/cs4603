# CS 4603 — PA1 Case Study · ** Model Answers & Discussion Guide**


**Fixed setup numbers:** context limit **8,192**; system prompt **500**; reply reserve **600**; warning at **80%**.

---

## Stage 1 — History & token budgeting

1. Running totals (cumulative):

| Call | prompt | completion | Running total |
|---|---|---|---|
| 1 | 520 | 180 | **700** |
| 2 | 900 | 300 | **1,900** |
| 3 | 1,600 | 500 | **4,000** |
| 4 | 2,200 | 700 | **6,900** |

2. Threshold = 0.80 × 8,192 = **6,553.6 → 6,554 tokens**.
3. Warning **first fires at Call 4**: running total 6,900 ≥ 6,554; usage = 6,900 / 8,192 = **≈ 84.2%**. (Calls 1–3 are below threshold: 8.5%, 23.2%, 48.8%.)
4. Protect the system prompt because: (i) it holds the assistant's **identity, rules, and format contract** (PTCF) — dropping it changes behaviour mid-conversation; (ii) it is **small and high-value**, so trimming it saves little budget but breaks everything downstream. Trim oldest *non-system* turns instead.

> **Discussion hook:** cumulative `prompt+completion` counts tokens already *spent*, not how full the *next* request will be — that sets up Stage 2. (See also the PA1 analysis question: this metric misses that each new call re-sends the whole surviving history plus the next user message and the not-yet-generated reply.)

**Carry forward:** threshold = 6,554; final total = 6,900.

---

## Stage 2 — Token pressure

1. Ratio = 1,050 / 280 = **3.75 characters/token**.
2. Manual tokens ≈ 30,000 / 3.75 = **8,000 tokens**.
3. Remaining context = 8,192 − 500 − 600 = **7,092 tokens**. The manual (8,000) does **not** fit — it overflows by **8,000 − 7,092 = 908 tokens**.
4. Any two of, with the tradeoff named:
   - **Truncate / trim** the manual → cheap and simple, but you may cut the exact section the user needs.
   - **Chunk + retrieve only relevant sections (RAG-style)** → keeps context small and relevant, but adds retrieval machinery and can miss chunks.
   - **Summarise/compress** the manual before injection → fits more content, but lossy and adds an extra LLM call/latency.
   - **Use a larger-context model** → fits everything, but higher cost/latency and doesn't fix lost-in-the-middle.
5. **Most efficient: Prose English** (4.1 / 4.4). **Least efficient: LaTeX math** (2.1 / 2.3). A big LaTeX block costs ~2× the tokens per character of prose, so it **eats the budget fast** → reserve more room, expect higher cost, or avoid pasting raw LaTeX.

**Carry forward:** ratio = 3.75; context room = 7,092 tokens.

---

## Stage 3 — Lost in the middle

1. Clear positional effect: **beginning 3/3 and end 3/3, middle only 1/3**. It is **not consistent** in the middle (fails 2 of 3), while the ends are consistent — a classic U-shaped recall curve.
2. Attention is distributed unevenly over long sequences: tokens near the **start and end** get stronger effective attention than the **middle**, so mid-context facts are more likely dropped. Implies "in context" ≠ "attended to".
3. Because refund/exception rules sit in the **middle sections**, Atlas may silently answer with the *general* policy and omit the exception — the engineer gets a confident, wrong-but-plausible answer with **no error raised**. Guideline: **place the most critical facts at the start or end of the context** (or retrieve just the relevant section rather than dumping the whole manual).

**Carry forward:** "the fact being present ≠ the model using it."

---

## Stage 4 — Model choice

1. Cost proxy = output tokens × rate:
   - **Model S** = 400 × ($0.50 / 1,000) = **$0.20**.
   - **Model L** = 450 × ($3.00 / 1,000) = **$1.35** (≈ 6.75× more expensive per response).
2. **TTFT** = time until the *first* streamed token appears; **total latency** also includes generating the *whole* answer. For a streaming chat box, TTFT drives **perceived responsiveness** — the user sees text appear quickly even if the full answer takes longer.
3. Pick **Model L** when quality/accuracy is critical and volume is low — e.g., drafting a customer-facing refund decision that must be right. Always pick **Model S** when latency/cost dominate and quality is "good enough" — e.g., high-volume autocomplete or internal quick lookups.
4. Two of: token pricing tiers/volume discounts; **input vs output** priced differently; separate charges for tool/function calls; infra/hosting or minimum-throughput costs; retries and failed calls; caching effects.

**Carry forward:** the chosen default model and the reason.

---

## Stage 5 — PTCF & F1

1. **Format** gives the biggest jump: baseline **0.41** → Format-only **0.71** = **+0.30** (vs Task +0.22, Context +0.17, Persona +0.03).
2. Exact-match scoring rewards output that is **structured and free of stray prose**. The Format pillar forces a predictable, parseable shape (e.g., strict `Record-ID → Sector` JSON), so the model stops emitting commentary that fails string equality — directly lifting exact matches.
3. Precision = 8 / 10 = **0.80**; Recall = 8 / 12 = **0.6667**; F1 = 2·(0.80·0.6667)/(0.80+0.6667) = 1.0667 / 1.4667 = **0.7273** (≈ 0.73).
4. A semantically correct value with a different surface form ("Fin-Tech" vs "Fintech", casing, trailing space) is scored **wrong**. Fuzzy matching would credit it but **risks false positives** — crediting near-but-wrong values — trading recall gains for weaker precision.

**Carry forward:** best variant (Full PTCF, or note the interference caveat); F1 formula.

---

## Stage 6 — Prompting strategies

1. For a **mechanical exact-extraction** task, CoT usually helps **little and can hurt**: the answer is a lookup, not multi-step reasoning, so added reasoning tokens invite drift/extra prose and consume context. CoT pays off on genuinely multi-step/reasoning tasks, not flat extraction.
2. Few-shot examples **compete with the haystack** for the fixed budget. Past the point where examples push the haystack beyond what fits comfortably, **recall drops** (truncated or middle-buried content). Test it by **sweeping shot count (0, 2, 5, …)** while holding the haystack fixed and plotting F1 vs shots — find where F1 turns down.
3. Ties to Stage 3: stuffing more few-shot examples into an already-full context both **shrinks room for the haystack** and **pushes real content toward the middle**, worsening lost-in-the-middle — so more examples can *reduce* recall even though they look helpful.

---

## Stage 7 — Tools & the multi-turn loop

1. A single call may **expose** tool schemas and *return* tool calls, but it does **not execute** them or continue. The **loop** runs the tool, feeds the result back, and re-calls the model until it returns final text. The surcharge question **requires** the loop because step 2 (compute the surcharge) **depends on** the result of step 1 (look up the fee) — two dependent steps.
2. Each result must carry its exact **`tool_call_id`**, so the model matches each returned value to the specific call that produced it.
3. Best answer: **pass the error back to the model** so it can correct and retry — e.g., document lookup returns "not found"/errors, and Atlas can rephrase the query or fall back to asking the user, rather than crashing the whole turn. (Accept "skip" or "stop" if justified for a specific safety-critical case.)
4. A bare exception **crashes the caller** with no usable output; the fixed fallback string degrades **gracefully and predictably**, so the UI/caller can show a clean message and the conversation state stays intact.

**Carry forward:** the full Atlas stack (model + PTCF + strategy + tool loop).

---

## Stage 8 — Failure modes & final integration

**Part A**
1. Validate **bad arguments before the tool runs** (a dedicated validation step / schema check at the boundary), so malformed input never reaches the tool and the model gets a clear, correctable error. (Defense-in-depth inside the tool is fine as a backup, but the boundary is the primary place.)
2. **Yes** — loop pressure can occur in normal use, not just tests. Requests that are **open-ended, under-specified, or need many sub-steps** (e.g., "reconcile all shipments this month") tend to trigger endless tool calls; that's why `max_turns` + a clean fallback exist.

**Part B**
3. **Condition 4 (Prompt + Strategy + Tools)** is the reliable minimum. Conditions 1–2 can fail because the fee **lives in the document**, not the prompt — without retrieval the model may **hallucinate** the figure, and without a calculator the 25% arithmetic can be wrong. Tie-ins: **Stage 3** — even if the manual is in context, a mid-document fee may be missed (lost in the middle), so targeted retrieval matters; **Stage 7** — the **tool loop** is what chains "look up the fee" → "compute the surcharge," which a single call can't do.
4. Condition 1 is right when the task is **low-stakes, latency/cost-critical, or not document-grounded** — e.g., a quick brainstorming or wording suggestion where retrieval and tools add cost and latency without improving correctness.

---

### Suggested in-class flow

- Run stages **live on the board**, filling the carry-forward numbers as a class so the dependencies are visible.
- After Stage 3, explicitly connect "present ≠ used" — it reframes every later stage.
- End on Stage 8 Part B: show that the **whole arc** (client → tokens → context → model → prompt → tools → failures)
  is exactly what the real PA1 codebase asks them to implement.

*End of model answers.*
