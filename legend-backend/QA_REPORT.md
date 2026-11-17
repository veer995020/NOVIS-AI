# QA report: provider pipeline

Generated from `qa_results.json` by `scripts/qa_benchmark.py`, and the test matrix in
`backend/tests/test_qa_matrix.py`. Every number here was measured by running the code on this machine.

## What was and was not tested

- **`MainProvider.py` is not in the project.** It was removed at your request. The provider code tested here is the
  generic pipeline (`services/chat.py`, `services/providers/base.py`, `reliability/classify.py`) and the provider
  contract, driven by scripted providers in `backend/tests/fakes.py`.
- **No comparison with Claude was run.** That needs calls to Anthropic's API, which requires a key and network access
  this environment does not have, and the provider was removed. No comparison figures are given.
- **Model quality is not measured.** Accuracy, reasoning, coding correctness, mathematics, hallucination rate and
  instruction-following by a model need a real model and a reference answer set. None was run.
- **Cost per successful task is not measured.** It needs a model, its prices and token counts. None was run.
- **Latency is pipeline overhead only.** The scripted provider returns at once, so these figures exclude model time.

## Test matrix

All 42 tests in test_qa_matrix.py pass on the final code. The full suite passes: 923 with the database, 897 without it.

| Category | What the tests check | Result |
|---|---|---|
| General questions | Replies reach the client byte-for-byte (short, unicode, markdown, 3000 chars) | Pass |
| Complex reasoning, coding, mathematics | Reasoning steps, LaTeX, Unicode maths, code blocks, JSON, SQL come back unchanged | Pass (pipeline fidelity only) |
| Instruction-following (transport) | Each mode gives the model a distinct system prompt; the user's words reach the model verbatim | Pass (not the model's compliance) |
| Long conversations | 40 turns: the prompt stays at most 7 messages, stored history capped | Pass |
| Context retention | Later turns receive earlier turns in order; two conversations never share messages | Pass |
| Structured outputs | Every success and error body validates against the schema; unknown fields refused with a structured 422 | Pass |
| API failures | Provider error, crash, empty reply, oversize reply: correct status and code, no internal detail in the body | Pass |
| Timeouts | A slow provider returns 504 GENERATION_TIMEOUT | Pass |
| Retries | A transient failure is retried; retries stop at the configured bound; rejected requests are never retried | Pass (failed before the fix) |
| Rate limits | Upstream rate limit: 429 with Retry-After. Our own limit: 429 with Retry-After after the configured count | Pass (upstream failed before the fix) |
| Streaming | start, then deltas that add up to the reply, then done; a failure mid-reply ends in done with status error | Pass |
| Concurrency | 20 turns on one conversation: one runs, the rest get 409; 25 separate conversations all succeed; the server turn limit refuses the overflow with 503 | Pass |

## Defects found and fixed

1. **Retries were configured and documented, but never performed.** `PROVIDER_MAX_ATTEMPTS` defaults to 3, and the
   README says transient failures are retried, but no code retried. A 30% per-attempt failure rate gave
   67.8% success with one attempt, which is what the code did before, since it never retried.
   *Fix:* a bounded retry in `services/chat.py`, for transient errors only, and only before any text has reached the
   client, inside one overall generation deadline so retries cannot outlast the turn's lease. After the fix the same
   load gives 96.4% success with three attempts.
2. **An upstream rate limit was reported as `502 GENERATION_FAILED`.** The client got no hint to wait and could retry
   straight away.
   *Fix:* a persisting upstream rate limit is `429 RATE_LIMITED` with `Retry-After` (5 seconds). The provider's own
   hint is not forwarded yet. The contract is bumped to version 8, because a changed status code is breaking under the
   contract's own rules.

Defects in my own work, found and corrected before the results above were taken:
- Several tests parsed responses with strict validation, which rejects the JSON's strings. Clients parse JSON text, so
  the tests now do the same.
- The stream test expected text without the trailing space that the provider itself emits.
- A rate-limit fake built its error without the required `retry_after` argument, so it reported crashes as rate limits.
  The first benchmark run showed this, and the corrected run is the one reported here.
- An early probe appeared to show a timeout returning success. The default timeout is 60 seconds and the fake sleeps for
  5, so the timeout never fired. With the minimum of 1 second, every request returns 504.

## Measurements (pipeline only, scripted provider)

**Latency, non-streaming, sequential, 400 requests:** p50 0.6 ms, p95 0.9 ms,
p99 1.4 ms. Success 100.0%.

**Concurrency, distinct conversations.** The provider takes 50 ms per reply, so p50 is that delay plus overhead:

| Concurrency | Requests | OK | Throughput (req/s) | p50 (ms) | p95 (ms) | Refused |
|---|---|---|---|---|---|---|
| 1 | 200 | 200 | 19.1 | 52.1 | 52.7 | none |
| 8 | 200 | 200 | 151.3 | 51.9 | 53.1 | none |
| 32 | 200 | 200 | 519.3 | 52.0 | 58.4 | none |
| 64 | 200 | 32 | 180.9 | 151.5 | 156.4 | 503 CAPACITY_EXCEEDED: 168 |

At 64 concurrent requests the server's turn limit (32) refuses the overflow with 503 CAPACITY_EXCEEDED, as designed.

**Concurrency, one conversation, 32 simultaneous turns:** 1 succeeded, 31 got 409 busy,
0 other. The stored version is 2, and the invariant "version = 1 + successful turns"
holds.

**Reliability under injected transient failures (500 requests each):**

| Failure rate per attempt | Max attempts | Requests | Success |
|---|---|---|---|
| 0.0% | 1 | 500 | 100.0% |
| 0.0% | 3 | 500 | 100.0% |
| 10.0% | 1 | 500 | 90.4% |
| 10.0% | 3 | 500 | 100.0% |
| 30.0% | 1 | 500 | 67.8% |
| 30.0% | 3 | 500 | 96.4% |

**Upstream rate limits, 3 attempts, 400 requests each:**

| Per-attempt rate-limit probability | Outcomes |
|---|---|
| 20.0% | 200: 396, 429: 4 |
| 60.0% | 200: 298, 429: 102 |

**Context: prompt size and latency as the conversation grows** (limit: 20 context messages):

| Stored turns | Prompt messages sent (min to max) | p50 latency (ms) |
|---|---|---|
| 0 | 2 to 21 | 0.8 |
| 10 | 21 to 21 | 0.8 |
| 50 | 21 to 21 | 0.8 |
| 100 | 21 to 21 | 0.8 |

The prompt never exceeds 21 messages (the system prompt plus 20 context messages), however long the conversation.

**Structured output:** 300 JSON objects sent as messages; 300 responses
validate against the schema; 300 replies parse back to the same object.

**Timeouts:** 10 of 10 slow requests returned 504 with a 1-second limit.

## The brief's metrics

| Metric | Status | Why |
|---|---|---|
| Accuracy | Not measured | Needs a model and reference answers |
| Coding correctness | Not measured (pipeline fidelity only: code blocks come back byte-for-byte) | Needs a model and test cases |
| Instruction-following | Not measured (transport only: the model receives the user's words and the mode's system prompt) | Needs a model |
| Hallucination rate | Not measured | Needs a model and a fact set |
| Latency | Pipeline overhead measured (above). Model and time-to-first-token not measured | No model; ASGI transport buffers streams, so first-byte timing is not measured |
| Reliability | Measured under injected failures (above) | Injected rates are not production rates |
| Cost per successful task | Not measured | Needs a model, its prices and token counts |

## Recommended next steps

1. Forward the provider's own `retry_after` in the 429 response, instead of the fixed 5 seconds.
2. Measure time to first token with a real HTTP server, since the ASGI transport buffers streamed bodies.
3. When a provider is registered, run the same prompt set against it and against a reference model. That is the only
   way to measure accuracy, coding correctness, hallucination and cost. Keep the same inputs for each, and record the
   token counts.
