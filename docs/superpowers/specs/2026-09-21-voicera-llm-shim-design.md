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

This is **Option A**, the integration approach chosen for the demo: a small service
speaking OpenAI chat-completions on the front and Agent Core on the back, with the client
configured to point its LLM provider at it. It is the fastest path to a working call and
needs no change to the client's pipeline.

The division of responsibility is fixed: **the client contributes the voice pipeline only**
— STT, TTS, VAD, turn-taking, transport, telephony — and **ai-diffusion owns all agent
logic**: config, tools, memory, knowledge, trust. The shim's job is confined to protocol
translation.

The shim is therefore specified here as a faithful implementation of a published HTTP
contract, not as an adapter to any one client's internals. Built to the contract it keeps
working as the client evolves, and it can be tested without the client present.

### Why the turn API, and not the LLM proxy

The obvious shortcut is ruled out. Agent Core exposes `POST /internal/llm/call`, but it
is a **bare provider passthrough** — no Trust Layer, no Memory, no NLU, no tool routing.
Routing conversational turns through it would bypass the Trust Layer on every turn, which
development guideline #4 forbids ("runs on every I/O pass, never skipped"). **The client
must reach Agent Core through the turn API, not the LLM proxy.**

### Throwaway by design

**The shim is throwaway.** It must not accrete features. Its replacement is the
`agent_core` provider inside the client's own registry (Option B, #376), and every feature
added here is migration debt.

Option A's accepted losses are barge-in cancellation, consent events and streaming
fidelity. This design recovers the third (§8); the first two stay out of scope.

---

## 2. Sources

**The HTTP contract** is taken from OpenAI's canonical machine-readable specification
(`https://raw.githubusercontent.com/openai/openai-openapi/master/openapi.yaml`, OpenAPI
3.1.0, retrieved 2026-09-21). The HTML reference pages at `developers.openai.com` and
`platform.openai.com` return 404 and 403 to automated fetches, so the machine-readable
source was used. It is the same contract.

**Agent Core's contract** is taken from the running service's own OpenAPI document and
`agent_core/src/models.py`.

Where this design differs from issue #369, it says so and gives the reason (§8, §12).

---

## 3. The contract we implement

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
`data: [DONE]\n\n`. Confirmed against the official client's own stream decoder.

### Errors — `ErrorResponse`

```json
{ "error": { "message": "...", "type": "...", "param": null, "code": null } }
```

All four inner fields are required by the schema.

---

## 4. The contract we call

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

Both endpoints are **direct mode**. Neither engages Agent Core's TurnAssembler, which is
reached only through the session API (`/sessions/{id}/input` + `/events`). That
distinction matters — see §8.

---

## 5. The impedance mismatch

Three differences between the two contracts drive the design.

| | OpenAI | Agent Core |
|---|---|---|
| **Memory** | Stateless. The client resends the full conversation every turn. | Stateful. Holds history, journey state and collected profile fields in Memory Layer, keyed on `session_id`. |
| **Identity** | No session concept in the request body. | Requires `session_id`; this domain also requires `user_id` (the caller's phone) for every Signals call. |
| **Tools** | The client declares tools and expects the model to call them. | Owns its own tools (`fetch_jobs`, `save_profile`, `apply_job`) and executes them internally. |

These are §11.1 to §11.4. Everything else is mechanical.

---

## 6. Surface

```
POST /v1/chat/completions
```

Authentication: a configured key, accepted as `Authorization: Bearer <key>` (OpenAI
convention). Requests without a valid key get `401` in the error envelope.

Both `stream: true` and `stream: false` are implemented (§8, §9). A faithful
implementation supports both, and `stream: false` directly serves the agreed working
practice of **debugging one system at a time** — proving KKB over `/process_turn` with
curl before any voice is involved.

---

## 7. Request translation

| Incoming | Treatment |
|---|---|
| `messages` | **§11.1 — open.** |
| `model` | Recorded and echoed back in the response; does not select a model. Agent Core owns model choice via domain config; the client's own LLM model setting is vestigial and merely points at the shim. |
| `stream` | Selects `/stream_turn` (true) or `/process_turn` (false). |
| `tools`, `tool_choice`, `functions`, `function_call` | **§11.4 — open.** |
| `n` | Values greater than 1 rejected with `400`; Agent Core produces one response. |
| `temperature`, `top_p`, `seed`, `max_tokens`, `frequency_penalty`, `presence_penalty`, `logit_bias`, `stop`, ... | Accepted and ignored. Agent Core owns sampling. Ignoring unsupported parameters is the norm for OpenAI-compatible servers and keeps clients working. |
| `stream_options.include_usage` | Honoured — adds the final usage chunk (§9). |
| everything else | Accepted and ignored. |

Unknown fields are ignored rather than rejected, so a newer client does not break.

### The client's system prompt is ignored

Settled by the agreed configuration-ownership split: the client's `system_prompt` is
**vestigial and left empty**, because Agent Core builds the real prompt.

The shim therefore ignores any `system` or `developer` message. Honouring one would put
two personas in competition with Agent Core's own persona and per-subagent prompts.

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

### `/stream_turn`, not `/process_turn` — and why this is not a departure

Agent Core's session API is ruled out, for a sound reason: running the client's
own turn assembly *and* Agent Core's TurnAssembler would double-batch and stack latency
against an 800–1200 ms per-turn budget. The client already performs turn assembly
competently, so **direct mode** is correct and the client should own turn-taking.

That argument is against **session mode**, and it holds. This design honours it in full:
no TurnAssembler, the client owns turn-taking.

But *direct mode* and *blocking* are two different things. `/stream_turn` is also direct
mode — it does not engage the TurnAssembler either (§4). It is the streaming variant of
exactly the mode already chosen. #369 carried forward `/process_turn`, the blocking
variant, and for a streaming client that is the wrong one of the two:

- `/process_turn` returns nothing until the whole turn completes, so a streaming client
  hears silence for the entire turn.
- Measured turn latency in the blue-dots web flow on 2026-09-21 was **4.0–6.2 s** (4878,
  4070, 4417, 4432, 4605, 6010, 6216 ms) against the **800–1200 ms** per-turn budget.
- With `/stream_turn` the first sentence is emitted as soon as it clears the trust check,
  so time-to-first-chunk is the time to the first sentence rather than the whole turn.
- `SentenceEvent` and `DoneEvent` map almost 1:1 onto chunks.

It also recovers one of Option A's three accepted losses — streaming fidelity. Barge-in
cancellation and consent events remain out of scope.

If barge-in is wanted later, `DELETE /sessions/{session_id}/active_turn` and the
`abort_event` hook in `agent_core/src/base.py` are the extension points.

---

## 9. Response translation — `stream: false`

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
                           ->  id = "chatcmpl-<generated>"
                           ->  object = "chat.completion"
                           ->  created = unix seconds
                           ->  usage (below)
```

`error_type` set produces the error envelope instead (§12).

**Usage.** `CompletionUsage` requires `prompt_tokens`, `completion_tokens`,
`total_tokens`. Agent Core's turn responses do not expose token counts, so the shim
reports zeros rather than omitting the object or inventing numbers. If per-turn token
accounting is wanted later it belongs in Agent Core's `DoneEvent`, not in the shim.

---

## 10. The greeting is not ours to emit

A direct-mode consequence the shim must not try to solve.

In direct mode Agent Core does **not** proactively emit the entry subagent's
`opening_phrase` — that is a session-mode feature (GH-149). The client's own greeting is
what plays, so KKB's opening phrase must be copied into the client's agent record, **or
the bot answers silently**.

The shim is request-response: it speaks only when spoken to. The first thing the caller
hears is the client's own greeting, configured on the client side. The client's
`greeting_message` is **required** for the demo — it is the only place the demo expresses
what the caller hears first.

Recorded here because "the bot answers silently" is a failure the shim cannot cause and
cannot fix, and would otherwise be debugged in the wrong place.

---

## 11. Open questions

### 11.1 What do we send to Agent Core from `messages[]`?

**The problem.** OpenAI is stateless, so a client sends the entire conversation on every
request and it grows each turn:

```
turn 1   [system, u1]
turn 2   [system, u1, a1, u2]
turn 3   [system, u1, a1, u2, a2, u3]
```

Agent Core is the opposite. The client keeps conversation state client-side; ai-diffusion
keeps it server-side in Memory Layer, and Agent Core's request takes a single
`user_message`. So both
sides are tracking the same conversation, and the client is telling us things Agent Core
already knows.

**Options.**

**(a) Send the last user message only.** Agent Core supplies the rest from its own
memory. Fits how Agent Core is built and is the only option that preserves journey state,
tool results and the collected profile across turns. Depends on §11.3 for a correct
session id.

**(b) Be genuinely stateless.** Forward the whole conversation each turn and hold nothing
server-side. Most faithful to the OpenAI contract. But `ProcessTurnRequest` has no field
for an inbound history, and journey state, subagent routing and collected fields are not
reconstructible from message text alone. This would require changing Agent Core, which is
out of scope for a throwaway shim.

**(c) Send the last message, and verify the rest.** As (a), but compare the earlier
messages against what Agent Core believes the history is and flag divergence. Catches a
client that reconnects, retries or replays — at the cost of bookkeeping.

**Suggestion: (a),** with (c) as a cheap safety net if replay proves to be a real problem
in testing. (b) is not achievable without changing Agent Core.

### 11.2 How does the caller's phone number reach the shim?

**This one is a requirement, not a choice.** The shim must receive the caller's phone
number on every request. The open part is only *how* it gets there.

Without it this domain does not degrade — it stops working. Every Signals connector is
keyed on it:

```
fetch_profile   GET  /admin/participant?phone_number={user_id}
save_profile    POST /admin/participant     phone_number = "+{user_id}"
apply_job       operates on the profile resolved from that number
```

An empty `user_id` produces `?phone_number=` (rejected upstream) or writes a participant
against a malformed number. This is not hypothetical: on 2026-09-21 the blue-dots web
flow sent a literal `"null"` where a caller identifier belonged and the upstream returned
422.

A `chat.completions` request has no field for this. The 37 request fields include `user`,
but it is optional, documented as a caching and abuse-detection hint, and being superseded
by `safety_identifier` / `prompt_cache_key` — not something to build on.

**Options for the transport.**

**(a) Alongside whatever already carries call identity.** The client already passes a
per-call identity to its LLM service. The same mechanism can carry the caller's number.
Consistent with how session identity is already solved, and nothing new to invent.

**(b) An HTTP header**, e.g. `X-User-Id`. Explicit, outside the request body so it does
not strain the OpenAI contract, and trivial for any client to set.

**(c) The `metadata` field** on the request body — a standard OpenAI field accepting up to
16 key-value pairs. The most contract-aligned option, since it is part of the published
schema.

**(d) Agent Core resolves it from the call id.** Rejected: no such mapping exists, and it
would add a dependency the shim cannot satisfy on its own.

**Suggestion: (a), with (c) as the fallback.**

Whichever is chosen must be **agreed with the client team and written into the integration
contract**, because the client is the only party that knows the caller's number. This is
the one item in this design that cannot be settled on our side alone.

### 11.3 Is `session_id` the phone number, or a separate per-call id?

Agent Core requires a `session_id` and keys all conversation memory on it — history,
journey state, collected profile fields, tool results. `ProcessTurnRequest` takes
`session_id` and `user_id` as **two separate fields**, so using different values for them
costs nothing structurally.

**(a) Use the phone number for both.** One value to obtain, one thing to agree with the
client, and §11.2 then covers everything.

The cost is that a phone number identifies a *person*, not a *conversation*. The same
caller ringing a second time reuses the session and **resumes the previous conversation
mid-flow** — the bot picks up at "what's your age?" instead of greeting them. Partly
mitigable: `ProcessTurnRequest` has a `fresh` flag to force a new session, triggered on an
idle gap. That is a heuristic, and choosing the gap correctly is guesswork.

**(b) Use a separate per-call identifier.** The client already passes a per-call identity
to its LLM service, so such a value exists on its side; `session_id` derives from it.

Correct by construction: the phone identifies the person and is stable forever, the
per-call id identifies one conversation and is new every call. No resume surprise, no idle
heuristic.

**Suggestion: (b).** Given the client can already supply a per-call identity, (b) is no
more work to obtain than (a) and removes a whole class of confusing behaviour.

**If (b) is chosen, the identifier must be named in the integration contract** — the shim
treats it as an opaque string and does not care about its format, but it must be stable
for the life of one conversation and unique across concurrent ones.

### Both values: fail fast when missing

If either `user_id` or `session_id` is absent or unusable, the shim returns `400` in the
error envelope and **does not call Agent Core**. Guessing, defaulting or proceeding with
an empty value is what produces the corrupted-record failures described in §11.2.

### 11.4 What do we do with `tools`?

**The problem.** An OpenAI client may declare `tools` and expects the assistant to
respond with `tool_calls` that the client then executes. Agent Core owns its own tools and
executes them internally; it never asks the caller to run anything.

Two things are settled either way:

- The client's tool definitions are **never forwarded** to Agent Core.
- Agent Core's internal tool calls (`fetch_jobs`, `save_profile`, `apply_job`) are
  **never surfaced** to the client. `was_tool_used` is metadata, not a `tool_calls`
  response.

**Options for the remaining question — may the shim ever emit `tool_calls`?**

**(a) Never.** Accept `tools`, ignore it, always return plain content. Simplest and
honest. But then nothing on the client side can be triggered by Agent Core. #369 requires
that `was_escalated` terminate the call, and for a telephony client the only in-band way
to do that is a tool call the client understands — so (a) means that requirement cannot
be met through the protocol and must be descoped.

**(b) Emit `tool_calls` only for a tool the client itself declared.** Never invent one.
When Agent Core signals a terminal condition (`was_escalated`, `session_ended`) and the
client has declared a matching tool, respond with `finish_reason: "tool_calls"` and that
tool. Stays within the contract — we only ever name something the client asked for.

**(c) Support tool calling generally.** Let Agent Core decide to call client tools, and
accept `role: "tool"` results back. Substantially more work, no current requirement, and
squarely the feature accretion a throwaway service must avoid.

**Suggestion: (b).** The rule is easy to state: *the shim may name a tool the client
declared; it may never invent one, and it never exposes Agent Core's internal tools.*

---

## 12. Error handling

Failures return the **OpenAI error envelope with real HTTP status codes**:

```json
{ "error": { "message": "...", "type": "...", "param": null, "code": null } }
```

| Condition | Status | `type` |
|---|---|---|
| Malformed body, missing `messages` or `model` | 400 | `invalid_request_error` |
| `n` greater than 1 | 400 | `invalid_request_error` |
| `user_id` or `session_id` missing (§11.2, §11.3) | 400 | `invalid_request_error` |
| Bad or missing key | 401 | `authentication_error` |
| Agent Core unreachable or timed out | 502 | `api_error` |
| `DoneEvent.error_type` or `ProcessTurnResponse.error_type` set | 502 | `api_error` |
| Unhandled shim fault | 500 | `api_error` |

All four inner fields are always present, with `param` and `code` null when not
applicable, as the schema requires.

For a mid-stream failure — where headers and some chunks have already been sent — an HTTP
status is no longer available. The stream terminates with a chunk carrying
`finish_reason: "stop"` followed by `[DONE]`, and the failure is logged.

> **Note for the reviewer.** #369 asks the shim to "map errors to a safe fallback
> utterance". This design deliberately does not: strict
> error semantics are what the OpenAI contract requires, and a client that receives a
> well-formed 502 can decide for itself what to say.
>
> The consequence should be accepted knowingly: a live voice caller experiences a 502 as
> silence or a dropped call rather than a spoken apology. If graceful degradation matters
> more than fidelity for the demo, the alternative is to return `200` with a fallback
> sentence for **backend** failures only, never for client errors. That is a deliberate
> deviation from the contract and should be recorded as such if chosen.

---

## 13. Placement

A wrapper on top of `reach_layer/web`, reusing its Agent Core client, configuration
loader and health surface. Whether it ships as extra routes on that service or as a
sibling module is an implementation-time decision; the design is identical either way.

New logic is small and separable:

| Unit | Responsibility |
|---|---|
| `routes` | `POST /v1/chat/completions`, auth, request validation |
| `identity` | Resolve `user_id` (§11.2) and `session_id` (§11.3); reject when absent |
| `translate_request` | OpenAI request to `ProcessTurnRequest` |
| `translate_response` | `ProcessTurnResponse` to `chat.completion` |
| `translate_stream` | `SentenceEvent` and `DoneEvent` to `chat.completion.chunk` plus `[DONE]` |

Caller-facing strings and the configured key come from configuration, never source
(`.claude/rules/configuration-discipline.md`).

---

## 14. Deployment

The shim runs on a shared demo VM alongside both stacks:

| Stack | Services | Budget |
|---|---|---|
| ai-diffusion (trimmed) | 9 — `agent_core`, `trust_layer`, `memory_layer`, `knowledge_engine`, `action_gateway`, `observability_layer`, `memgraph`, `redis`, `otelcol` | ~2.5 CPU / 2.5 GB |
| VoicEra (trimmed) | 7 — `postgres`, `ferretdb`, `redis`, `api`, `minio`, `minio-init`, `runtime` | ~3 CPU / 4 GB |
| VM | **4 vCPU / 16 GB / 100 GB** | "comfortable for the demo" |

The shim is an additional service on top of the nine.

> **Discrepancy to resolve.** Issue #31 states *"Blue-dots footprint is 2 CPU / 4 GB"* and
> frames the ~2.5 CPU estimate as exceeding a *"confirmed 2 CPU / 4 GB allocation"*. The
> analysis specifies a 4 vCPU / 16 GB VM and calls it comfortable. These cannot both be
> current. If the epic's smaller allocation is the real one, CPU headroom is a genuine
> risk and the shim adds to it; if the larger VM is provisioned, it is not a concern.

CPU scales with concurrent calls, since VAD runs per call; size up beyond 2–3
simultaneous callers.

---

## 15. Risks

**The open questions in §11 are blocking for a working call.** The caller's phone
number especially: without it the domain's Signals calls cannot function.

**Unverified end to end.** No OpenAI client has yet been pointed at a running shim. The
contract is read from the canonical specification, but a live interop check with the
official `openai` Python client — both stream modes — should be the first implementation
step, before any client-side integration. This follows the working practice of debugging
one system at a time.

**Throwaway status.** #376 / Option B replaces this. Anything beyond implementing the
contract should be refused.

---

## 16. Testing (#370)

Driven by the official `openai` Python client, so the tests prove contract compliance
rather than compliance with one consumer's quirks. No VoicEra required — which is the
point of #370.

| Area | Check |
|---|---|
| Non-streaming | `stream=False` returns a valid `ChatCompletion`; `object`, `id`, `created`, `model` and all four `choices[0]` fields present |
| Streaming | `stream=True` yields valid `ChatCompletionChunk` objects, content in order, terminal `[DONE]`; the SDK's own parser accepts every chunk |
| SSE framing | Raw bytes are `data: {json}\n\n`, ending `data: [DONE]\n\n` |
| Usage | With `stream_options.include_usage`, a final usage chunk appears with all three required fields |
| Errors | Each row of §12 returns the right status and a well-formed envelope with all four inner fields |
| Identity | Absent identity gives 400, and **no** Agent Core call |
| Session continuity | Multi-turn: one `session_id` throughout, Agent Core sees one session, history not double-counted |
| Concurrency | Two interleaved conversations never share state |
| Latency | **Time-to-first-chunk** recorded against the 800–1200 ms budget, reported separately from whole-turn latency |

Time-to-first-chunk is the number that matters for a voice client and is the one #370's
"latency per turn" wording obscures. Both should be recorded.

---

## 17. Out of scope

- Option B / #376 — the `agent_core` provider in the client's registry
- Barge-in cancellation and consent events — accepted Option A losses
- The greeting (§10) — configured on the client side
- `/v1/models`, `/v1/completions` and every other OpenAI endpoint
- Multi-tenancy; conversations are identified by `session_id`, callers by `user_id`
- Token accounting (§9)

---

## Appendix — reaching the shim from a client that cannot set `base_url`

A deployment concern, not a design one.

Some clients expose a settable endpoint only on their Azure provider configuration, in
which case a zero-change variant exists via Azure wire-format emulation. The cleaner route
is a small `base_url` field on the client's OpenAI configuration — a change of roughly five
lines on their side.

Neither belongs on the critical path. If that change is slow to land, emulating Azure's
deployment-scoped URL scheme instead buys total independence from the review cycle, at an
estimated cost of about two extra days.

Should the fallback be needed, it means serving an additional URL shape
(`/openai/deployments/{deployment}/chat/completions?api-version=...`) and accepting an
`api-key` header in place of `Authorization: Bearer`. Everything else in this design is
unchanged, since the request and response bodies are identical. It should be added only
if a specific deployment requires it.
