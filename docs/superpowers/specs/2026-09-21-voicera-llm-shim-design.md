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

These are §11.1 to §11.5. Everything else is mechanical.

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
| *(not from the request)* `channel` | **§11.4 — open.** Required by Agent Core; omitting it fails the turn. |
| `tools`, `tool_choice`, `functions`, `function_call` | **§11.5 — open.** |
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

#### Why we need it

The caller's phone number **is the user's identity** in this domain. It is not a
convenience or a logging field — it is the primary key every downstream write is made
against.

Agent Core passes it to the Action Gateway as `user_id`, and the Signals connectors
substitute it directly into the upstream calls:

```
fetch_profile   GET  /admin/participant?phone_number={user_id}
                     -> does this caller already have a profile?

save_profile    POST /admin/participant     phone_number = "+{user_id}"
                     -> creates or updates the participant record

apply_job       POST /action/perform
                     -> submits an application on the profile resolved from that number
```

So the number decides *whose* profile is read, *whose* record is written, and *who* the
job application is submitted for. A conversation without it cannot do the one thing this
domain exists to do.

Nor can it be collected during the conversation. The caller is never asked for their own
number — the domain's prompts forbid it, because on a phone call the platform already
knows who dialled and asking would be absurd. The number has to arrive with the request.

**What happens without it.** Not graceful degradation — corruption. An empty `user_id`
renders as `?phone_number=` (rejected upstream) or writes a participant against a
malformed number. This is not hypothetical: on 2026-09-21 the blue-dots web flow sent a
literal `"null"` where a caller identifier belonged and the upstream returned 422.

#### Why we will not get it by default

**A normal OpenAI call carries no phone number.**

The premise of this shim is that the client points at us believing we are OpenAI and
changes nothing else. That premise holds everywhere except here. An LLM has no reason to
be told who is on the phone, so nothing in the chat-completions contract carries it. Of
the 37 request fields there is no caller-identity field — `user` exists but is optional,
is documented as a caching and abuse-detection hint, and is being superseded by
`safety_identifier` / `prompt_cache_key`, so it is not something to build on.

**If the client calls us exactly as it calls OpenAI, the shim receives nothing that
identifies the caller.** That cannot be worked around on our side: the value was never
sent, and no amount of inference recovers it.

#### Therefore

This is a **requirement on the client, not a design choice for us**. The client must
deliberately send the caller's phone number on every request. The only open part is
*which mechanism* it uses.

It is the one item in this design that cannot be settled on our side alone, and the one
place where "point at us and change nothing" does not hold. It needs agreeing with the
client team and writing into the integration contract before implementation starts.

#### Options for the transport

All four require the client to do something deliberate; there is no option in which the
number simply arrives.

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

**Suggestion: (a), with (c) as the fallback.** (a) reuses a path the client already has;
(c) is the most contract-aligned, since `metadata` is a published OpenAI field.

### 11.3 Is `session_id` the phone number, or a separate per-call id?

The same gap applies: **chat-completions has no session concept.** It is stateless by
design — the client resends the whole conversation each turn precisely because the server
is not expected to remember anything, so there is no conversation identifier in the
request.

Agent Core is the opposite. It requires a `session_id` and keys all conversation memory on
it — history, journey state, collected profile fields, tool results. `ProcessTurnRequest`
takes `session_id` and `user_id` as **two separate fields**, so using different values for
them costs nothing structurally.

Unlike §11.2 there is a fallback that needs nothing further from the client: if the phone
number is being sent anyway, it can serve as the session key too. That is option (a), and
it has a real cost.

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

### 11.4 Which `channel` do we declare — `voice` or `web`?

**Why it matters.** `ProcessTurnRequest.channel` is not a label. Agent Core uses it to
select which `system_prompt_suffix` and `tts_rules` apply to the turn, and the two
candidates instruct the model to do **opposite** things:

| channel | what the model is told |
|---|---|
| `voice` | ~2,000 chars of phone rules plus `tts_rules` for numbers, money, dates, time, phone, email, abbreviations and output script. Write numbers **as words**. No markdown. |
| `web` | *"You are in a text chat, not on a phone call. The user READS your reply."* Digits, markdown lists, bold. |

**Omitting it is a hard failure**, not a fallback. `channel` defaults to `None` and Agent
Core raises:

```python
config = channels.get(channel)
if config is None:
    raise ValueError(f"Unsupported channel: {channel}")
```

So the shim must send something, and the something decides how every reply sounds.

**A distinction worth stating, because it is easy to conflate:** where the shim's code
lives and what channel it declares are independent. The shim is a wrapper on top of
`reach_layer/web` (§13), but `channel` describes **the medium the caller is using**, not
the service hosting the shim. Hosting inside `reach_layer/web` does not oblige us to send
`channel: "web"`.

**Options.**

**(a) `voice`.** Correct for the actual caller: someone on a phone who will *hear* the
reply. The model writes numbers as words and avoids markup, which is what a TTS engine
needs.

**(b) `web`.** Matches where the code is hosted, and is the natural default if the shim is
built as web routes without thinking about it. It is wrong for the caller: the model is
explicitly told the user will *read* the reply, so it produces markdown bullets and digit
strings — and a telephony client will read `**Titan Retail**` and `₹27,620` aloud, markup
and all.

**(c) A new channel, e.g. `shim` or `telephony`.** Cleanest in principle — the domain
config could carry rules tuned for this path specifically. But it means adding a channel
block to every domain config that wants to use the shim, and for a throwaway service that
is configuration debt for no behavioural gain over `voice`.

**Suggestion: (a) `voice`.** The caller is on a phone; the channel should say so.

This choice also settles the TTS-sanitizer question (§15): with `voice`, the model is
already instructed to emit speech-ready text, so a sanitizer is optional insurance. With
`web` it would be mandatory, because markdown would arrive on every single turn.

### 11.5 Session end — the closing word and the hang-up

#### How it works today

When a conversation reaches a natural end, three things happen in sequence:

```
caller: "thank you, bye"
   |
   v
the LLM calls the internal `end_session` tool
   |
   v
Agent Core intercepts it and sets  session_ended = true
   |
   v
the VOICE REACH LAYER reacts to that flag by doing two things:
   1. appends `channels.voice.terminal_word` to the outbound speech
   2. closes the transport, so the call actually hangs up
```

The third step is the **reach layer's** responsibility. Agent Core only decides; something
downstream carries it out.

This is live in this domain today: `conversation.session_end_eval.enabled: true`, with the
LLM explicitly instructed to call `end_session` on "thank you", "bye", "bas ho gaya",
"alvida", and `channels.voice.terminal_word: "Thank you"`.

#### What is missing here

**In this topology there is no reach layer.** The client replaces it. So when the LLM ends
the session, the flag reaches the shim and nothing acts on it: no closing word is spoken,
and the call stays open with the caller sitting in silence after saying goodbye.

The shim inherits both jobs. They are separable, so they are decided separately.

#### Part A — who speaks the closing word?

**(a) The shim appends it.** On `DoneEvent.session_ended`, emit
`channels.voice.terminal_word` as one final content chunk before closing the stream.
Reproduces today's behaviour exactly, and the value is already in domain config.

**(b) Nobody.** The conversation simply stops after the last real sentence. Acceptable if
the agent's own final reply already reads as a goodbye — but `terminal_word` exists
because it often does not.

**(c) The client says it.** Configure a closing phrase on the client side. Splits one
behaviour across two systems and drifts from the domain config.

**Suggestion: (a).** It is a few lines, it matches current behaviour, and the value is
already configured.

#### Part B — who hangs up? (and what we do with `tools`)

Hanging up is not something the shim can do directly — it has no control over the
caller's line. It can only send the client something the client acts on, which makes this
the same question as what we do with `tools`.

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

**The caller's phone number is the top risk, and it is not ours to close.** A standard
OpenAI call does not carry one, so unless the client is changed to send it deliberately,
the shim never receives it and every Signals call in this domain fails (§11.2). This is
an integration-contract item that needs agreeing with the client team **before**
implementation, not discovered during call testing. Everything else in this design can
proceed without it; a working call cannot.

**The other open questions in §11 are also blocking** for a working call, but each can be
settled on our side or with a small agreement.

**Unverified end to end.** No OpenAI client has yet been pointed at a running shim. The
contract is read from the canonical specification, but a live interop check with the
official `openai` Python client — both stream modes — should be the first implementation
step, before any client-side integration. This follows the working practice of debugging
one system at a time.

**No TTS sanitizer anywhere in this path — accepted.** `TTSTextSanitizerProcessor`
(markdown and emoji to spoken text, Devanagari-safe) is a Pipecat processor inside
`reach_layer/voice`, which is not deployed here, and the client has no equivalent. Agent
Core's text therefore reaches the client's TTS unmodified.

With `channel: "voice"` (§11.4) the model is already instructed to emit speech-ready
text, so this is insurance rather than a gap — and the decision is to **go without it**,
per the rule that a throwaway service should not accrete features. The accepted failure
mode: when the model disobeys its own formatting rules, the caller hears the markup. On
2026-09-21 the model produced a numbered list where the config demanded bullets and
silently dropped one of four items, so this is a real behaviour, not a theoretical one.
Ugly and recoverable, not data-corrupting. Revisit if it proves frequent on real calls.

**Caller hang-up never reaches Agent Core.** If the caller drops mid-conversation, the
shim simply stops receiving requests; nothing informs Agent Core and the session state
lingers. Self-correcting if `session_id` is a per-call value (§11.3 option b) — the next
call starts fresh regardless. Persistent if `session_id` is the phone number, where the
next call resumes a half-finished conversation.

**Client request timeout against turn latency.** Measured turns are 4-6 s. If the
client's HTTP timeout is below that, turns fail before Agent Core answers. Streaming
mitigates this only if the client's timeout applies to time-to-first-byte rather than to
the whole response. Worth confirming with the client team rather than assuming.

**One domain per Agent Core deployment.** Agent Core serves a single domain
configuration, so one shim instance fronts one domain. `ProcessTurnRequest` carries
`caller_agent_id`, but nothing uses it for routing today. Adequate for the demo; stated
so it is not mistaken for multi-tenancy.

**Reply language against TTS voice** — client-side, noted for completeness. This domain
mirrors the caller into Hindi or Gujarati mid-conversation, while the client's TTS voice
is configured per agent. A Hindi reply may be spoken by a voice configured for English.
Nothing the shim can influence.

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
