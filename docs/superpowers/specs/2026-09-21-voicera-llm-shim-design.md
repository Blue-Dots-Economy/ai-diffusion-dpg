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
| *(not from the request)* `channel` | Set by the shim to its own channel name (§11.4). Required by Agent Core; omitting it fails the turn. |
| `tools`, `tool_choice`, `functions`, `function_call` | Accepted and discarded; the shim never emits `tool_calls` (§11.5). |
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

Two remain open — §11.1 and §11.2. The rest (§11.3, §11.4, §11.5 Part B) have been
decided and are recorded below with their consequences.

### 11.1 What do we send to Agent Core from `messages[]`?

**Status: open, pending confirmation from the client team.**

OpenAI is stateless, so a client normally sends the entire conversation on every request
and it grows each turn:

```
turn 1   [system, u1]
turn 2   [system, u1, a1, u2]
turn 3   [system, u1, a1, u2, a2, u3]
```

Agent Core is the opposite. It stores history, journey state, collected profile fields
and tool results in Memory Layer, and its request takes a single `user_message`. Passing
the whole array would double-count everything Agent Core already knows.

**Preferred: ask the client to send only the newest user message.** The shim then passes
`messages[-1].content` straight through with no interpretation. Simplest on our side and
unambiguous — there is no guessing about which message is "the new one".

**Fallback, if they decline or cannot:** the shim extracts the last `user`-role message
itself and discards the rest. Functionally equivalent for well-formed input, but it puts
the burden of interpretation on us and is fragile if a client ever sends two user
messages in a row, replays after a reconnect, or reorders.

Either way the earlier messages are discarded; the question is only who does the
discarding. **To be confirmed with the client team before implementation starts.**

Note that a client sending only the last message is no longer behaving like a standard
OpenAI client — the same caveat as §11.2. If that ask is refused, the fallback works.

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
be told who is on the phone, so nothing in the chat-completions contract carries it.

**If the client calls us exactly as it calls OpenAI, the shim receives nothing that
identifies the caller.** That cannot be worked around on our side: the value was never
sent, and no amount of inference recovers it.

#### Therefore

This is a **requirement on the client, not a design choice for us**. The client must
deliberately send the caller's phone number on every request. The only open part is
*which mechanism* it uses, and that has to be agreed with them.

#### Options to put to the client team, in preference order

All require the client to do something deliberate; there is no option in which the number
simply arrives. Listed so that if one is refused, the next can be proposed.

**1. `metadata` — recommended.** A published, optional field on the request body:

> Set of 16 key-value pairs that can be attached to an object. This can be useful for
> storing additional information about the object in a structured format.

Keys up to 64 characters, values up to 512. It is the only field in the contract designed
for arbitrary per-request data, so nothing is being repurposed:

```json
"metadata": { "caller_phone": "919900112233" }
```

**2. An HTTP header**, e.g. `X-Caller-Phone` and `X-Session-Id`. Outside the request body
entirely, so it places no strain on the OpenAI contract at all, and any HTTP client can
set one. The constraint is whether the client's LLM configuration exposes custom headers
on outbound calls — worth asking before proposing it.

**3. `prompt_cache_key`.** A plain `string | null` that is not deprecated and carries no
instruction to transform the value. Its documented purpose is cache bucketing —
*"Replaces the `user` field"* — so using it for identity is a repurposing, but a harmless
one: a stable per-caller key is exactly the shape it expects.

> The `user` field was considered and rejected. It is the obvious identity slot —
> *"a stable identifier for your end-users"* — but the schema marks it
> `"deprecated": true`, and asking a partner team to adopt a deprecated field invites
> pushback and creates future migration work.

**4. `safety_identifier` — works, but carries a trap.** A `string | null`, max 64
characters, so a phone number fits and would reach us intact. But the field's own
documentation says:

> We recommend **hashing** their username or email address, in order to avoid sending us
> any identifying information.

A client team following that guidance will hash the value, and we would receive an opaque
digest that cannot be used for `?phone_number=`. It only works if they knowingly send the
raw number against the field's stated intent. If this option is chosen, that caveat must
be stated explicitly in the request to them.

**Not viable: a custom message role.** Adding something like
`{"role": "contact", "content": "<phone>"}` to `messages[]` looks attractive but the role
enum is closed — `developer`, `system`, `user`, `assistant`, `tool`, `function`. A
conformant client SDK rejects an unknown role before the request is sent, so this would
require the client to bypass its own SDK. It would also put caller PII into the
conversation transcript.

### 11.3 What is `session_id`? — DECIDED

**Decision: the phone number, the same value as `user_id`.** No separate per-call
identifier is requested from the client.

#### Why this is right, and why the obvious objection does not hold

The objection to reusing the phone is that it identifies a *person*, not a *conversation*,
so a caller ringing back would resume their previous conversation rather than starting
afresh. That is true — and for this domain it is the desired behaviour, not a defect. A
caller whose line drops mid-application should continue where they left off, not answer
everything again.

It is also less consequential than it first appears, because the two kinds of state are
stored separately and only one of them is keyed on the session:

| Keyed on | Holds | Survives into a new session? |
|---|---|---|
| `user_id` | name, age, gender, location, trade, plus the previous journey summary | **Yes** |
| `session_id` | current subagent, pending question, fetched job list, `selected_job_item_id`, `profile_action` | No |

At session start, Memory Layer hydrates a new session for a returning caller from the
persistent profile (`memory_layer/src/memory_layer.py:250-259`):

```python
if is_returning:
    profile = self._user_store.get_profile(user_id)
    journey = self._journey_store.get_last_journey_summary(user_id, session_id)
    for field_name in self._declared_fields:
        val = profile.get(field_name)
        if val is not None:
            initial_state[field_name] = str(val)
```

So a returning caller is **never re-asked their profile questions**, whatever `session_id`
is. The choice is not "resume versus start from scratch" — it is only whether the
*conversation* continues.

#### The TTL is the boundary

The domain already sets this deliberately:

```yaml
ttl_minutes: 2880    # 2 days — caller can resume next day if dropped.
```

Within two days the session is still in Redis and the conversation resumes. After that it
has expired, and the caller gets a clean conversation with their profile still known. The
idle boundary is therefore already configured and does not need inventing, and
`ProcessTurnRequest.fresh` is not needed.

#### Consequences, accepted

**A stale job list inside the TTL window.** `last_jobs` and `selected_job_item_id` persist
with the session, so a caller who browsed jobs on Monday and rings back on Tuesday resumes
against Monday's search results — the agent could ask "shall I submit your application?"
about a listing from the previous call. Job postings change slowly, so within two days
this is minor. If it proves to be a problem, the fix belongs in the domain config (expire
the job list sooner than the session) rather than in the shim.

**Two simultaneous calls from one number would share a session.** Unlikely on telephony,
and the only case a per-call identifier would have prevented. Recorded rather than
designed around.

### Both values: fail fast when missing

If the caller's phone number is absent or unusable, the shim returns `400` in the error
envelope and **does not call Agent Core**. `session_id` is derived from the same value
(§11.3), so one missing input fails both. Guessing, defaulting or proceeding with
an empty value is what produces the corrupted-record failures described in §11.2.

### 11.4 Which `channel` do we declare? — DECIDED

**Decision: a new Reach Layer channel of its own, alongside `web` and `voice`.**

`ProcessTurnRequest.channel` is not a label. Agent Core uses it to select which
`system_prompt_suffix` and `tts_rules` apply, and the existing two instruct the model to
do opposite things:

| channel | what the model is told |
|---|---|
| `voice` | ~2,000 chars of phone rules plus `tts_rules` for numbers, money, dates, time, phone, email, abbreviations and output script. Write numbers **as words**. No markdown. |
| `web` | *"You are in a text chat, not on a phone call. The user READS your reply."* Digits, markdown lists, bold. |

Omitting it is a hard failure, not a fallback — `channel` defaults to `None` and Agent
Core raises `ValueError(f"Unsupported channel: {channel}")`.

Neither existing value is right. `web` is wrong for a caller who will *hear* the reply —
they would be read `**Titan Retail**` and digit strings aloud. `voice` is closer but
assumes the voice Reach Layer is downstream, and it is not: in this topology there is no
TTS sanitizer to clean up after the model (§15), so this path needs its own, stricter
rules.

So the shim becomes **a third Reach Layer channel in its own right**, not a variant of
either. Reach Layer gains a fourth text surface alongside `web`, `voice` and `cli`, and
the domain config gains a matching `channels.<name>` block whose prompt rules are tuned
for it — speech-ready output like `voice`, with no reliance on downstream sanitizing.

**Naming: `bridge`.** The channel must stay generic. Although it speaks OpenAI's API
today, that is an implementation detail of the current client, and naming the directory
after one vendor would wrongly imply the channel is only ever for them. `bridge` names
the role instead — an external platform reaching Agent Core through a translated
protocol — and survives the protocol changing.

`custom` was considered. It is accurate but tells a future reader nothing about what the
directory does.

The same name is used for the directory (`reach_layer/bridge/`) and for the
`channels.bridge` key in domain config, so there is one concept with one name.

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

**In this topology the client replaces the voice reach layer.** So when the LLM ends the
session, the flag reaches the shim and nothing acts on it.

#### Part A — who speaks the closing word? — open, low stakes, ours to decide

**(a) The shim appends it.** On `DoneEvent.session_ended`, emit the channel's
`terminal_word` as one final content chunk before closing the stream. Reproduces today's
behaviour, and the value is already in domain config.

**(b) Nobody.** The conversation stops after the last real sentence. Acceptable if the
agent's final reply already reads as a goodbye — but `terminal_word` exists because it
often does not.

**Suggestion: (a).** A few lines, matches current behaviour, and given Part B below it is
the *only* signal the caller gets that the conversation has finished.

#### Part B — who hangs up, and what about `tools`? — DECIDED

**Decision: the shim ignores `tools` entirely and never emits `tool_calls`.** The client
is not expected to send any; if it does, they are accepted and discarded.

This keeps the rules simple and absolute:

- The client's tool definitions are **never forwarded** to Agent Core, which owns its own
  tools.
- Agent Core's internal tool calls (`fetch_jobs`, `save_profile`, `apply_job`) are
  **never surfaced** to the client. `was_tool_used` is metadata, not a `tool_calls`
  response.
- The shim **never emits** `tool_calls`. Every response is plain content.

**The consequence, accepted knowingly: the shim cannot hang up the call.** A tool call was
the only in-band mechanism available — the shim has no control over the caller's line and
can only send the client something the client acts on. With tool calls ruled out, there is
no such signal.

So after the agent finishes and speaks its closing word, **the line stays open until the
client's own idle or session-timeout handling ends it.** Whatever call-ending behaviour
the client already has is what terminates the call. This also means #369's requirement
that `was_escalated` terminate the call **is not met by this design** and should be
treated as descoped rather than outstanding.

If clean hang-up later proves necessary, the options are to revisit tool calls, or to have
the client end the call on its own signal — and the right long-term answer is #376, where
a native provider can drive termination directly.

## 12. Error handling

Failures return the **OpenAI error envelope with real HTTP status codes**:

```json
{ "error": { "message": "...", "type": "...", "param": null, "code": null } }
```

| Condition | Status | `type` |
|---|---|---|
| Malformed body, missing `messages` or `model` | 400 | `invalid_request_error` |
| `n` greater than 1 | 400 | `invalid_request_error` |
| Caller's phone number missing (§11.2) | 400 | `invalid_request_error` |
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

**A new Reach Layer channel in its own right** (§11.4), alongside `web`, `voice`, `cli`
and `mcp` — named `reach_layer/bridge/` (§11.4). It follows the shape the existing
channels use (`Dockerfile`, `main.py`, `pyproject.toml`, `src/`, `tests/`) and reuses the
shared `reach_layer/base` config loader and Agent Core client rather than reimplementing
them.

It is not built on top of `reach_layer/web`. It needs its own `channels.<name>` block in
the domain config, because neither the `web` nor the `voice` prompt rules are right for a
caller who hears the reply through a client that does no TTS sanitizing.

New logic is small and separable:

| Unit | Responsibility |
|---|---|
| `routes` | `POST /v1/chat/completions`, auth, request validation |
| `identity` | Resolve the caller's phone into `user_id` and `session_id` (§11.2, §11.3); reject when absent |
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

The shim is an additional service on top of the nine — one more Reach Layer channel.

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
lingers. With `session_id` being the phone number (§11.3), the next call
resumes that conversation — intended for a dropped call, and bounded by the 2-day session
TTL after which it expires and the caller starts clean.

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
