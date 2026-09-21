# OpenAI-compatible LLM Shim — Design

**Issue:** ai-diffusion-dpg#369 (sub-issue of bluedots-program#31, FN-AI-VOICERA)
**Related:** #370 (test harness), #376 (the production replacement), #366 (trimmed stack)
**Status:** design — not implemented
**Date:** 2026-09-21

---

## 1. Purpose

A service that implements **OpenAI's `POST /v1/chat/completions`** and is backed by Agent
Core instead of a model.

```
any OpenAI client  ──▶  [ SHIM ]  ──▶  Agent Core  ──▶  Trust / Memory / Action Gateway
   (VoicEra today)        │
                          └── translates the request in, and the response back out
```

The client believes it is talking to OpenAI. It is configured with our base URL and
changes nothing else.

The immediate consumer is VoicEra, which needs to drive a KKB / blue-dots call while
Agent Core keeps ownership of every turn — VoicEra runs its LLM inside its own voice
service, which inverts this framework's architecture (conflict A in
`docs/voicera-telephony-adapter-gap-analysis.md`). But **the shim is not a VoicEra
adapter.** It is a faithful implementation of a published HTTP contract. Anything
VoicEra-specific is a deployment note (Appendix B), not part of the design.

That distinction is deliberate: built to the contract, the shim keeps working when
VoicEra changes, and any OpenAI-compatible client can drive it. Built to VoicEra's
current internals, it would be brittle and single-purpose.

### Throwaway by design

The production target is #376 — an `agent_core` provider inside VoicEra's own registry.
Every feature here is migration debt. The design implements the contract and nothing
beyond it.

---

## 2. The contract we implement

Taken from OpenAI's canonical machine-readable specification
(`https://raw.githubusercontent.com/openai/openai-openapi/master/openapi.yaml`,
OpenAPI 3.1.0, retrieved 2026-09-21). This is the same contract rendered at
`developers.openai.com/api/reference/.../chat/completions/create`; the HTML pages return
404/403 to automated fetches, so the machine-readable source was used.

### Request — `CreateChatCompletionRequest`

37 fields. **Required: `messages`, `model`.** All others optional.

Accepted message roles: `developer`, `system`, `user`, `assistant`, `tool`, `function`.

### Response — `chat.completion` (non-streaming)

```
required: id, object, created, model, choices
choices[]: { index, message, finish_reason, logprobs }     <- all four required
optional:  usage, system_fingerprint, service_tier, metadata
object == "chat.completion"
```

### Response — `chat.completion.chunk` (streaming)

```
required: id, object, created, model, choices
choices[]: { index, delta, finish_reason }                 <- all three required
delta:     content | role | tool_calls | function_call | refusal
object == "chat.completion.chunk"
```

`finish_reason` is one of `stop`, `length`, `tool_calls`, `content_filter`,
`function_call`.

Tool-call fragments are `ChatCompletionMessageToolCallChunk` — only `index` is required;
`id`, `type` and `function` are optional, which is what allows arguments to be streamed
across several chunks.

### Wire format

SSE. Each event is `data: {json}\n\n`; the stream terminates with a literal
`data: [DONE]\n\n`. Confirmed against the official client's own decoder
(`openai/_streaming.py` — `sse.data.startswith("[DONE]")`, events delimited by `\n\n`).

### Errors — `ErrorResponse`

```json
{ "error": { "message": "...", "type": "...", "param": null, "code": null } }
```

All four inner fields are required by the schema.

---

## 3. The contract we call

Agent Core, verified against the live service's OpenAPI:

| Endpoint | Shape |
|---|---|
| `POST /process_turn` | Blocking. Returns `ProcessTurnResponse`. |
| `POST /stream_turn` | SSE. `SignalEvent`* then `SentenceEvent`* then a terminal `DoneEvent`. |

```
ProcessTurnRequest   session_id*, user_message*, user_id, channel,
                     caller_agent_id, timestamp_ms, fresh, locale, metadata
ProcessTurnResponse  session_id, response_text, was_escalated, was_tool_used,
                     model_used, latency_ms, error_type, error_message
```

`SentenceEvent{text, sentence_index}` carries one trust-checked sentence.
`DoneEvent{was_escalated, session_ended, was_tool_used, model_used, latency_ms,
turn_status, error_type, error_message}` is always terminal
(`agent_core/src/models.py:248-307`).

---

## 4. The impedance mismatch

Three differences between the two contracts drive the whole design.

| | OpenAI | Agent Core |
|---|---|---|
| **Memory** | Stateless. The client resends the full conversation every turn. | Stateful. Holds history, journey state and collected profile fields in Memory Layer, keyed on `session_id`. |
| **Identity** | No session concept. No caller identity. | Requires `session_id`; this domain also requires `user_id` (the caller's phone) for every Signals call. |
| **Tools** | The client declares tools and expects the model to call them. | Owns its own tools (`fetch_jobs`, `save_profile`, `apply_job`) and executes them internally. |

These are the three open questions in §10. Everything else in this design is mechanical.

---

## 5. Surface

```
POST /v1/chat/completions
```

Authentication: a configured key, accepted as `Authorization: Bearer <key>` (OpenAI
convention). Requests without a valid key get `401` in the error envelope.

Both `stream: true` and `stream: false` are implemented (§7, §8). A faithful
implementation supports both, and `stream: false` makes the service testable with plain
`curl`.

---

## 6. Request translation

| Incoming | Treatment |
|---|---|
| `messages` | **§10.1 — open.** |
| `model` | Recorded and echoed back in the response; does not select a model. Agent Core owns model choice via domain config. |
| `stream` | Selects `/stream_turn` (true) or `/process_turn` (false). |
| `tools`, `tool_choice`, `functions`, `function_call` | **§10.3 — open.** |
| `n` | Values greater than 1 rejected with `400`; Agent Core produces one response. |
| `temperature`, `top_p`, `seed`, `max_tokens`, `frequency_penalty`, `presence_penalty`, `logit_bias`, `stop`, ... | Accepted and ignored. Agent Core owns sampling. Ignoring unsupported parameters is the norm for OpenAI-compatible servers and keeps clients working. |
| `stream_options.include_usage` | Honoured — adds the final usage chunk (§8). |
| everything else | Accepted and ignored. |

Unknown fields are ignored rather than rejected, so a newer client does not break.

---

## 7. Response translation — `stream: false`

`POST /process_turn` maps to one `chat.completion` object.

```
ProcessTurnResponse            chat.completion
--------------------------     -----------------------------------------
response_text              ->  choices[0].message.content
                           ->  choices[0].message.role = "assistant"
                           ->  choices[0].index = 0
                           ->  choices[0].logprobs = null      (required field)
                           ->  choices[0].finish_reason = "stop"
model_used                 ->  model
session_id                 ->  (not exposed; internal)
                           ->  id = "chatcmpl-<generated>"
                           ->  object = "chat.completion"
                           ->  created = unix seconds
                           ->  usage (§9)
```

`error_type` set produces the error envelope instead (§11).

---

## 8. Response translation — `stream: true`

`POST /stream_turn` maps to a sequence of `chat.completion.chunk` events.

```
                              first chunk:  delta.role = "assistant"
SignalEvent          ->       ignored (logging only)
SentenceEvent        ->       delta.content = event.text,  finish_reason = null
SentenceEvent        ->       delta.content = event.text,  finish_reason = null
DoneEvent            ->       delta = {},  finish_reason = "stop"
                              usage chunk if stream_options.include_usage
                              data: [DONE]
```

Every chunk carries the same `id`, `object: "chat.completion.chunk"`, `created` and
`model`, and `choices[0].index = 0`.

**Why `/stream_turn` and not `/process_turn`.** #369 names `/process_turn`. That endpoint
blocks until the whole turn completes, so a streaming client receives nothing until the
end and any first-token latency target is unreachable. Measured turn latency in the
blue-dots web flow on 2026-09-21 was **4.0-6.2 s** (4878, 4070, 4417, 4432, 4605, 6010,
6216 ms) against the **800-1200 ms** target in #370. With `/stream_turn` the first
sentence is emitted as soon as it clears the trust check, so time-to-first-chunk is the
time to the first sentence rather than the whole turn. `SentenceEvent` and `DoneEvent`
also map almost 1:1 onto chunks.

The specification is corrected here deliberately: **#369's choice of `/process_turn` is
wrong.**

Session mode (`POST /sessions/{id}/input` plus `GET /sessions/{id}/events`) was considered
and rejected: it streams equally well and adds native barge-in, but requires a long-lived
subscription per conversation and event-to-turn correlation — too much machinery for a
throwaway service. If barge-in is needed later,
`DELETE /sessions/{session_id}/active_turn` and the `abort_event` hook in
`agent_core/src/base.py` are the extension points.

---

## 9. Usage

`CompletionUsage` requires `prompt_tokens`, `completion_tokens`, `total_tokens`. Agent
Core's turn responses do not expose token counts, so the shim reports zeros rather than
omitting the object or inventing numbers. Clients that read usage get a well-formed
object; none of them depend on the values being non-zero.

If per-turn token accounting is wanted later, it belongs in Agent Core's `DoneEvent`, not
in the shim.

---

## 10. Open questions

### 10.1 What do we send to Agent Core from `messages[]`?

**The problem.** OpenAI is stateless, so a client sends the entire conversation on every
request and it grows each turn:

```
turn 1   [system, u1]
turn 2   [system, u1, a1, u2]
turn 3   [system, u1, a1, u2, a2, u3]
```

Agent Core is the opposite. It stores history, journey state, collected profile fields
and tool results in Memory Layer against a `session_id`, and its request takes a single
`user_message`. The two models overlap: the client is telling us things Agent Core
already knows.

**Options.**

**(a) Send the last user message only.** Agent Core supplies the rest from its own
memory. Fits how Agent Core is built and is the only option that preserves journey state,
tool results and the collected profile across turns. Depends entirely on §10.2 — without
a correct session id, Agent Core either sees no history or sees the wrong caller's.

**(b) Be genuinely stateless.** Forward the whole conversation each turn and hold nothing
server-side. Most faithful to the OpenAI contract. But `ProcessTurnRequest` has no field
for an inbound history, and journey state, subagent routing and collected fields are not
reconstructible from message text alone. This would require changing Agent Core, which is
out of scope for a throwaway shim.

**(c) Send the last message, and verify the rest.** As (a), but compare the earlier
messages against what Agent Core believes the history is and flag divergence. Catches a
client that reconnects, retries or replays — at the cost of bookkeeping.

**Suggestion: (a),** with (c) as a cheap safety net if replay turns out to be a real
problem in testing. (b) is not achievable without changing Agent Core.

**Also to decide:** what the shim does with a client-supplied `system` message. Agent
Core has its own persona and per-subagent prompts tuned for the domain; honouring a
client system prompt would put two personas in competition. The suggestion is to ignore
it, but that should be a conscious choice rather than an omission.

### 10.2 Where does the session id — and the caller's identity — come from?

**The problem.** `ProcessTurnRequest` requires `session_id`, and this domain requires
`user_id` (the caller's phone number): the Signals connectors substitute it into
`?phone_number={user_id}` and `"+{user_id}"`, so `fetch_profile`, `save_profile` and
`apply_job` all depend on it.

**A `chat.completions` request carries neither.** The protocol has no session concept,
and nothing in the 37 request fields identifies an end user in a way we can rely on
(`user` exists but is documented as a cache and abuse-detection hint, is optional, and is
being superseded by `safety_identifier` / `prompt_cache_key`).

Getting this wrong has two failure modes, and the second is serious:

- Key too unstable, and a fresh session is created per turn, so Agent Core never sees
  history and the conversation loops.
- Key too coarse, and two concurrent callers share a session. In this domain that means
  one caller's details written against another's phone number, and a job application
  submitted on the mixed-up profile.

**Options.**

**(a) An HTTP header** such as `X-Session-Id` and `X-User-Id`. Clean, explicit, outside
the OpenAI body so it does not violate the contract, and trivially supported by any
client that can set headers. Requires the client to set them.

**(b) Carry them inside the request body** — a documented marker in the system message,
or an agreed key in `metadata` (a standard OpenAI field: up to 16 key-value pairs).
`metadata` is the most contract-aligned place. Requires the client to populate it.

**(c) Derive them from the conversation** — hash a stable prefix of `messages[]`, or match
an incoming array against stored histories. **Not recommended.** A bot with a scripted
opening produces byte-identical arrays across different callers for the first several
turns, so two callers are indistinguishable exactly when it matters most. This is the
cross-contamination case above.

**(d) One conversation at a time.** No identification; a single active session with an
idle reset. Adequate only for a single-user demo.

**Suggestion: (a) or (b) — the client must supply identity**, with `metadata` preferred
because it is part of the published contract. Whichever is chosen, `user_id` and
`session_id` should be **separate values**: the phone identifies the person and is stable
forever; the session identifies one conversation. Using the phone for both means a second
call from the same person resumes the first conversation mid-flow.

**Missing identity means fail fast.** If identity is absent, return `400` in the error
envelope and do not call Agent Core. Proceeding would leave `user_id` empty, and the
Signals connectors would then issue `?phone_number=` or write a participant against a
malformed number. This is not hypothetical: on 2026-09-21 the blue-dots web flow sent a
literal `"null"` as `acting_as_user_id` and the upstream returned 422.

### 10.3 What do we do with `tools`?

**The problem.** An OpenAI client may declare `tools` and expects the assistant to
respond with `tool_calls` that the client then executes. Agent Core owns its own tools and
executes them internally; it never asks the caller to run anything.

So there is a genuine semantic gap: the client offers capabilities we cannot use, and we
have internal tool activity the client must not see.

Two things are settled either way:

- The client's tool definitions are **never forwarded** to Agent Core.
- Agent Core's internal tool calls (`fetch_jobs`, `save_profile`, `apply_job`) are
  **never surfaced** to the client. `was_tool_used` is metadata, not a `tool_calls`
  response.

**Options for the remaining question — may the shim ever emit `tool_calls`?**

**(a) Never.** Accept `tools`, ignore it, always return plain content. Simplest and
honest. But then nothing on the client side can ever be triggered by Agent Core. Notably
#369 requires that `was_escalated` terminate the call, and for a telephony client the
only in-band way to do that is a tool call the client understands — so (a) means that
requirement cannot be met through the protocol.

**(b) Emit `tool_calls` only for a tool the client itself declared.** Never invent one.
When Agent Core signals a terminal condition (`was_escalated`, `session_ended`) and the
client has declared a matching tool, respond with `finish_reason: "tool_calls"` and that
tool. Stays within the contract — we only ever name something the client asked for — and
gives Agent Core a way to drive a client-side action.

**(c) Support tool calling generally.** Let Agent Core decide to call client tools, and
accept `role: "tool"` results back. Substantially more work, no current requirement, and
squarely migration debt for a throwaway service.

**Suggestion: (b).** It satisfies #369's termination requirement without inventing
protocol, and the rule is easy to state: *the shim may name a tool the client declared;
it may never invent one, and it never exposes Agent Core's internal tools.* If (a) is
chosen instead, #369's call-termination requirement should be explicitly descoped.

---

## 11. Error handling

Failures return the **OpenAI error envelope with real HTTP status codes**:

```json
{ "error": { "message": "...", "type": "...", "param": null, "code": null } }
```

| Condition | Status | `type` |
|---|---|---|
| Malformed body, missing `messages` or `model` | 400 | `invalid_request_error` |
| `n` greater than 1 | 400 | `invalid_request_error` |
| Identity missing (§10.2) | 400 | `invalid_request_error` |
| Bad or missing key | 401 | `authentication_error` |
| Agent Core unreachable or timed out | 502 | `api_error` |
| `DoneEvent.error_type` or `ProcessTurnResponse.error_type` set | 502 | `api_error` |
| Unhandled shim fault | 500 | `api_error` |

All four inner fields are always present, with `param` and `code` null when not
applicable, as the schema requires.

For a mid-stream failure — where headers and some chunks have already been sent — an
HTTP status is no longer available. The stream terminates with a chunk carrying
`finish_reason: "stop"` followed by `[DONE]`, and the failure is logged. Silently
truncating a stream is the only option the protocol leaves; inventing content to explain
the error would be worse.

> **A note for the reviewer.** Strict error semantics are correct for the contract, and
> that is what this specifies. It has a consequence worth accepting knowingly: a live
> voice caller experiences a 502 as silence or a dropped call rather than a spoken
> apology. If graceful degradation matters more than fidelity for the PoC, the
> alternative is to return `200` with a fallback sentence for **backend** failures only
> (never for client errors). That is a deliberate deviation from the contract, not an
> oversight, and should be recorded as such if chosen.

---

## 12. Placement

A wrapper on top of `reach_layer/web`, reusing its Agent Core client, configuration
loader and health surface. Whether it ships as extra routes on that service or as a
sibling module is an implementation-time decision; the design is identical either way.

New logic is small and separable:

| Unit | Responsibility |
|---|---|
| `routes` | `POST /v1/chat/completions`, auth, request validation |
| `identity` | Resolve `user_id` and `session_id` (§10.2); reject when absent |
| `translate_request` | OpenAI request to `ProcessTurnRequest` |
| `translate_response` | `ProcessTurnResponse` to `chat.completion` |
| `translate_stream` | `SentenceEvent` and `DoneEvent` to `chat.completion.chunk` plus `[DONE]` |

Caller-facing strings and the configured key come from configuration, never source
(`.claude/rules/configuration-discipline.md`).

---

## 13. Risks

**The three open questions in §10 are all blocking for a working call.** Identity
especially: without it the domain's Signals calls cannot function at all.

**Service count against the CPU budget.** #366 specifies a trimmed 9-service stack and
drops all reach layers. The shim reintroduces one, making ten, on a stack the analysis
already budgets at ~2.5 CPU against a confirmed 2 CPU allocation. Memory is comfortable;
CPU is not.

**Throwaway status.** #376 replaces this with adapter interfaces on both sides. Anything
beyond implementing the contract should be refused.

**Unverified end to end.** No real OpenAI client has yet been pointed at a running shim.
The contract is taken from the canonical specification, but a live interop check with the
official `openai` Python client — both stream modes — should be the first implementation
step, before any client-specific integration.

---

## 14. Testing (#370)

Driven by the official `openai` Python client, so the tests prove contract compliance
rather than compliance with one consumer's quirks. No VoicEra required.

| Area | Check |
|---|---|
| Non-streaming | `client.chat.completions.create(stream=False)` returns a valid `ChatCompletion`; `object`, `id`, `created`, `model` and all four `choices[0]` fields present |
| Streaming | `stream=True` yields valid `ChatCompletionChunk` objects, content in order, terminal `[DONE]`; the SDK's own parser accepts every chunk |
| SSE framing | Raw bytes are `data: {json}\n\n`, ending `data: [DONE]\n\n` |
| Usage | With `stream_options.include_usage`, a final usage chunk appears with all three required fields |
| Errors | Each row of §11 returns the right status and a well-formed envelope with all four inner fields |
| Identity | Absent identity gives 400, and **no** Agent Core call |
| Session continuity | Multi-turn: one `session_id` throughout, Agent Core sees one session, history not double-counted |
| Concurrency | Two interleaved conversations never share state — the cross-contamination case in §10.2 |
| Latency | **Time-to-first-chunk** recorded against the 800-1200 ms target, reported separately from whole-turn latency |

Time-to-first-chunk is the number that matters for a voice client and is the one #370's
"latency per turn" wording obscures. Both should be recorded.

---

## 15. Out of scope

- Option B / #376 — the `agent_core` provider in the client's own registry
- Barge-in and `cancel_turn`
- `/v1/models`, `/v1/completions` and every other OpenAI endpoint
- Multi-tenancy; conversations are identified by `session_id`, callers by `user_id`
- Token accounting (§9)

---

## Appendix A — client compatibility notes

Observations from the client that will drive the shim first. These are **not** part of
the contract; implementing §2 correctly satisfies them. Recorded so a reviewer can see
they were checked.

VoicEra reaches its LLM through Pipecat's `OpenAILLMService`. From
`pipecat/services/openai/base_llm.py`:

- It always sends `stream: True` and `stream_options: {"include_usage": True}`.
- It sends `messages`, `tools`, `tool_choice`, `model` and the sampling parameters. It
  does **not** send `user`.
- Its chunk loop never reads `finish_reason`. It detects a tool call purely from
  `delta.tool_calls` being present.
- It captures `tool_call.id` only inside the branch guarded by `tool_call.function.name`,
  so a conformant implementation must emit `id` and `function.name` in the **same** chunk
  — which is what OpenAI itself does.
- Tool-call coalescing keys on `tool_call.index`.
- Chunks with empty `choices` and chunks with an empty `delta` are skipped, so a
  usage-only final chunk and a role-only opening chunk are both safe.
- `stop_ttfb_metrics()` fires on the first chunk carrying choices, so the client's own
  time-to-first-byte metric keys off our first chunk.

## Appendix B — reaching the shim from a client that cannot set `base_url`

Purely a deployment convenience; no bearing on the design.

Some clients expose a configurable endpoint only on their Azure provider. Pipecat's
`AzureLLMService` builds
`{endpoint}/openai/deployments/{model}/chat/completions?api-version=...` and authenticates
with an `api-key` header instead of `Authorization: Bearer`.

If such a client must be supported, serving that additional URL shape and accepting that
header is a small addition — one route and one header name — and requires no change on
the client side. It is **not** required by this design and should only be added if a
specific deployment needs it.

> #369 states the shim is blocked until the client's OpenAI config accepts a custom
> `base_url`, budgeting roughly two extra days to emulate Azure's URL scheme otherwise.
> That dependency is smaller than stated: the Azure shape is already available and
> emulating it is trivial.
