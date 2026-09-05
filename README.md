# Vera-replacement bot — approach

## What this is

A deterministic, rule-based composer — **no LLM call at all**. `compose(category,
merchant, trigger, customer)` dispatches on `trigger["kind"]` (26 kinds observed
in the dataset) to a small template function that reads facts straight out of
the four pushed JSON contexts and formats them into a WhatsApp message.

`bot.py` is a ~150-line FastAPI server wiring the 5 required endpoints
(`/v1/context`, `/v1/tick`, `/v1/reply`, `/v1/healthz`, `/v1/metadata`, plus
optional `/v1/teardown`) around in-memory context/conversation stores.
`conversation_handlers.py` holds the multi-turn state machine: auto-reply
detection with send→wait→end escalation, hostile-message graceful exit, and
intent-transition routing (switches straight to action mode on an explicit
merchant go-ahead instead of re-qualifying).

## Why no LLM

- **Determinism is free.** The brief requires `compose()` be deterministic;
  a pure function of its inputs gets that for nothing, vs. pinning
  `temperature=0` and hoping a provider's API is bit-for-bit stable.
- **Anti-fabrication is structural, not a prompt instruction.** Every
  template only ever reads fields that exist on the input dicts — there's
  nothing to hallucinate. `h_supply_alert`, for instance, explicitly declines
  to guess an affected-customer count it has no batch-dispense data to derive.
- **No API key, no privacy surface, no latency risk.** Sub-30s response is
  trivial; nothing leaves the process.
- **Every trigger kind gets deliberate handling.** All 26 kinds in the
  dataset (`research_digest`, `perf_spike/dip`, `recall_due`,
  `competitor_opened`, `ipl_match_today`, ... ) have a dedicated handler,
  plus a generic fallback for any unmapped kind that anchors on the
  merchant's strongest available performance signal rather than going bland.

## Trade-offs

- **Phrasing is templated, not generated.** It can't match an LLM's range of
  natural phrasing across truly novel combinations. It's been checked against
  all 30 canonical test pairs and all 100 dataset triggers and reads
  naturally in every case observed, but a genuinely new `trigger.kind` not in
  this dataset would hit the generic fallback rather than a bespoke voice.
- **Hindi-English code-mixing is targeted, not full-sentence.** Numbers,
  names, and facts stay language-neutral; only the closing CTA line swaps to
  a hand-written Hinglish phrasing when `languages`/`language_pref` includes
  `hi`. This matches the real Vera examples' pattern (facts stay
  English/numeric, the *ask* mixes in Hindi) but is less rich than a model
  generating a fully-mixed sentence.
- **Off-topic detection is a small keyword list** (GST, loan, insurance,
  visa...), not semantic understanding — a genuinely novel curveball
  question outside that list falls through to the generic engaged-reply
  branch rather than being explicitly declined.
- **Two placeholder-payload triggers of the same kind for the same merchant
  can produce byte-identical bodies** (5 such pairs in the full 100-trigger
  dataset) — when the trigger payload is `{"placeholder": true}` with zero
  real signal, there's genuinely nothing to differentiate on without
  fabricating something. Verified via stress-test; not fixed further because
  fabricating a differentiator would score worse against the anti-fabrication
  rule than an occasional repeat.

## What additional context would have helped most

- A `now` timestamp passed into `compose()` (not just into `/v1/tick`) so
  `seasonal_beats` could be matched to the actual current month instead of
  defaulting to the category's first listed beat.
- A batch-dispense log for pharmacies, so `supply_alert` could state exactly
  how many customers are affected by a specific recalled batch (currently
  conservatively omitted rather than guessed).
- Richer `available_slots`/`next_session_options` payloads across more
  trigger kinds (currently only `recall_due` and `trial_followup` carry them
  in this dataset) — booking-flow CTAs read stronger with real slots than
  the generic "want me to hold a slot" fallback.

## Files

- `bot.py` — FastAPI server, the 5 endpoints.
- `composer.py` — `compose()`, pure and deterministic.
- `conversation_handlers.py` — `respond(state, merchant_message)`, the
  multi-turn state machine.
- `submission.jsonl` — rendered by `build_submission.py` against the 30
  canonical test pairs from `../expanded/test_pairs.json` (produced by
  running `dataset/generate_dataset.py`, which is deterministic — every
  participant gets the same 30 pairs).
- `build_submission.py` — dev utility, not a required deliverable; regenerate
  with `python build_submission.py` after any composer change.

## Running it

```bash
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Verified end-to-end against a live server: full 255-context warmup, all 100
dataset triggers ticked with zero crashes/URLs/empty bodies, `/v1/context`
idempotency (409 on stale version, 400 on invalid scope), and all three
Phase-4 replay scenarios (auto-reply escalation send→wait→end, intent
transition to action mode, hostile graceful exit).
