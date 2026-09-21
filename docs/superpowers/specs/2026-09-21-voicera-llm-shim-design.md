# VoicEra LLM Shim — Design

**Issue:** ai-diffusion-dpg#369 (sub-issue of bluedots-program#31, FN-AI-VOICERA)
**Related:** #370 (test harness), #376 (the production replacement), #366 (trimmed stack), #372 (VoicEra capability map)
**Status:** design — not implemented
**Date:** 2026-09-21

---

## 1. Purpose

Let VoicEra run a KKB / blue-dots call end to end while Agent Core keeps ownership of
every turn.

VoicEra's `voice_2_voice_server` runs its LLM *inside* the voice service as a Pipecat
processor. This framework's rule is the opposite: the voice channel is a transport and
all LLM calls belong to Agent Core. `docs/voicera-telephony-adapter-gap-analysis.md`
records this as conflict A and concludes their voice service cannot be adopted whole.

The shim sidesteps the conflict without either side rewriting anything. VoicEra already
knows how to call an OpenAI-compatible LLM, so we present ourselves as one:

```
caller speaks
     │
     ▼
VoicEra  (STT → "LLM" → TTS)
     │  believes it is calling OpenAI
     ▼
  [ SHIM ]  ──translates──▶  Agent Core  ──▶  Trust / Memory / Action Gateway / Signals
```

VoicEra is pointed at our address and otherwise behaves exactly as it does today.

### Throwaway by design

The production target is #376 — an `agent_core` provider inside VoicEra's own registry.
Every feature added to the shim is migration debt. This design deliberately omits
anything not needed to complete one call.

---

## 2. Evidence base

The epic cites three design documents:

- `2026-09-09-voicera-integration-analysis.md`
- `2026-09-09-voicera-integration-action-plan.md`
- `2026-09-09-voicera-integration-estimate.md`

**None of these exist.** Searched: every object in every ref of this repo
(`git rev-list --all --objects`), the exact filenames across all history, the
`docs/superpowers/specs/` listing on `main` (37 files, none dated later than
2026-05-20), the whole local filesystem, `bluedots-program` (an empty repository —
issues only), and `bluedots-docs`. The three names appear only in the bodies of
bluedots-program#31 and #32. `sanketika-labs/ai-diffusion-dpg` redirects to
`Blue-Dots-Economy/ai-diffusion-dpg` — a rename, not a second location.

GitHub code search returned nothing for them, but it also returned nothing for
`voicera-telephony-adapter-gap-analysis`, a file that demonstrably exists in this repo.
Code search is not indexing these repositories, so those negatives carry no weight and
were discarded.

Because the cited analysis is unavailable, this design was built by reading source
directly. Every claim below is traceable:

| Claim | Source |
|---|---|
| OpenAI provider exposes no `base_url` | `COSS-India/VoicEra` `apps/providers/cloud/openai/config.py` — *"API host is Pipecat's OpenAI default"* |
| Azure provider exposes a settable endpoint | `apps/providers/cloud/azure_openai/config.py` — `endpoint: str = Field(...)`; `model` documented as *"Azure deployment name"* |
| Azure URL construction | `apps/providers/cloud/azure_openai/service.py` → `AzureLLMService(api_key, endpoint, settings)` |
| Exact params Pipecat puts on the wire | `pipecat/services/openai/base_llm.py:320` (read from `reach_layer/voice/.venv`) |
| `{{var}}` substitution into the system prompt | `apps/runtime/services/pipecat/audio.py` — `substitute_variables`, `resolve_custom_variables` |
| Per-call values override agent defaults | same file — `return {**config_vars, **call_vars}` |
| Phone pipeline resolves those variables | `apps/runtime/routes/agent.py:184` |
| Outbound accepts `custom_variables` | `apps/api/app/routers/calls.py` — `create_outbound_call(..., custom_variables=body.custom_variables)` |
| Inbound does not | same file — `register_inbound_call(org_id, agent_id, provider_call_sid, from_number, to_number)` |
| Inbound via websocket discards the number | `apps/runtime/routes/agent.py:149` — `from_number="unknown"` |
| Call ending is an LLM tool call | `apps/runtime/services/pipecat/call_ending.py` |
| Agent Core turn endpoints and event models | `agent_core/src/models.py:248-307`, live OpenAPI at `/openapi.json` |

`COSS-India/VoicEra` is a public repository.

---

## 3. What VoicEra actually sends

From `pipecat/services/openai/base_llm.py:320`, the complete request:

```python
params = {
    "model", "stream": True, "stream_options": {"include_usage": True},
    "frequency_penalty", "presence_penalty", "seed", "temperature",
    "top_p", "max_tokens", "max_completion_tokens", "service_tier",
    "messages", "tools", "tool_choice",
}
```

Three consequences:

1. **No `user` field.** Confirmed absent, not merely unlikely. It cannot carry identity.
2. **`stream` is always `True`.** Only the streaming path needs to work.
3. **Everything except `messages` is per-agent configuration** — byte-identical on every
   call from every caller. The conversation content is the only thing that varies.

---

## 4. Placement

The shim is a **wrapper on top of `reach_layer/web`**, reusing its Agent Core client,
configuration loader and health surface. Whether it ships as extra routes on that
service or as a sibling module is an implementation-time decision; the design is
identical either way.

New logic is small and separable:

| Unit | Responsibility |
|---|---|
| `identity` | Extract `user_id` / `session_id` from the request; reject when absent |
| `translator` | `SentenceEvent` / `DoneEvent` ⇄ chat-completion chunks |
| `routes` | The two OpenAI-compatible URL surfaces |

---

## 5. URL surfaces

```
POST /v1/chat/completions
POST /openai/deployments/{deployment}/chat/completions?api-version=...
```

The second is the one that matters. VoicEra's `azure_openai` provider has a settable
`endpoint`, and Pipecat builds `{endpoint}/openai/deployments/{model}/chat/completions`.
An operator points that at the shim and the integration works **with no code change on
VoicEra's side at all**.

`{deployment}` and `api-version` are accepted and ignored. Authentication is accepted as
either `api-key` (Azure convention) or `Authorization: Bearer` (OpenAI convention), and
validated against a configured key.

> **#369 states the shim is blocked until VoicEra's OpenAI LLM config accepts a custom
> `base_url`, with an estimated two extra days to emulate Azure's URL scheme otherwise.
> That dependency does not exist.** The Azure provider already accepts an arbitrary
> endpoint, and matching its URL shape is one route plus one header name. The shim is
> not blocked on any VoicEra code change for outbound calls.

---

## 6. Request translation

| Incoming | Treatment |
|---|---|
| last `user` message | → `user_message` |
| `messages[0]` (system) | discarded, except the identity marker (§7) |
| earlier `messages[]` | discarded — Agent Core owns history; forwarding it would double-count every turn |
| `tools` | scanned for `end_conversation` only (§9); never forwarded to Agent Core |
| `temperature`, `model`, `top_p`, `seed`, `max_tokens`, … | ignored — Agent Core owns model choice and sampling |
| `stream: false` | rejected with 400; Pipecat never sends it |

### VoicEra's system prompt is discarded

VoicEra ships a per-agent `system_prompt`. Agent Core has its own persona and
per-subagent prompts, tuned for this domain. Forwarding VoicEra's would put two personas
in competition and reintroduce conflict C from the gap analysis (configuration scope
belongs to Agent Core, not the voice service).

The prompt is therefore dropped — but it remains the **transport** for per-call data,
because it is the only channel VoicEra has (§7). The VoicEra agent prompt becomes a
small documented config contract rather than a second persona.

### Agent Core's tool calls are never surfaced

Agent Core calls `fetch_jobs`, `save_profile`, `apply_job` and others internally. None of
these are visible to VoicEra. The only `tool_calls` the shim ever emits is
`end_conversation` (§9).

---

## 7. Caller identity — OPEN QUESTION

Agent Core requires a `session_id`, and this domain requires the caller's phone number as
`user_id`: the Signals connectors substitute it into `?phone_number={user_id}` and
`"+{user_id}"`, so `fetch_profile`, `save_profile` and `apply_job` all depend on it.

Chat-completions is stateless and carries neither.

### The transport

VoicEra's system prompt supports `{{variable}}` placeholders substituted from
`custom_variables`, which merge agent defaults with per-call overrides (call wins). The
operator puts an agreed marker line in the agent prompt:

```
[dpg] phone={{phone}} call={{call_id}}
```

The substituted values arrive inside `messages[0]`. The shim reads that line and ignores
the rest of the prompt. This is configuration only — no VoicEra code change.

### The open decision

> **Should `user_id` and `session_id` both be the phone number, or should `session_id` be
> VoicEra's per-call `call_id`?**
>
> **(a) Phone for both.** Simplest. One value to pass, one thing to configure. But the
> phone identifies the *person*, not the *call*: a second call from the same seeker
> reuses the session and resumes the first conversation mid-flow — the bot picks up at
> "what's your age?" instead of greeting them. Mitigable by forcing a fresh session after
> an idle gap (`ProcessTurnRequest.fresh` exists), but that is a heuristic.
>
> **(b) `user_id` = phone, `session_id` = `call_id`.** Correct by construction: the phone
> is stable forever and identifies the person for Signals; the call id is new every call
> and scopes the conversation. Agent Core already takes both as separate fields. VoicEra
> already generates a `call_id` per call (`apps/runtime/routes/agent.py`), so asking for
> both is no more operator work than asking for one.
>
> Recommendation is (b), but this is left for the reviewer.

### Rejected alternatives, recorded so they are not re-invented

**Conversation-prefix matching.** Remember each live session's history; match an incoming
request to the session whose stored history is a prefix of the incoming `messages[]`,
since each turn grows the array by one assistant reply plus one user message.

Rejected: a voice bot opens with a *scripted greeting*. Two callers who both answer
"hello" produce byte-identical arrays for the first several turns, and the shim cannot
tell them apart until their answers diverge. That is precisely the cross-contamination
case — one caller's details saved against another's phone number, and a job application
submitted on the mixed-up profile. Unacceptable for a flow that writes real records.

**Connection identity.** Treat one HTTP connection as one call. Pipecat builds its client
once per call, so turns likely reuse one connection — but this is unverified and breaks
behind any proxy or load balancer that pools connections.

**The `user` field.** Confirmed absent from Pipecat's request (§3).

### Missing identity — fail fast

**If the marker is absent or unparseable, the shim returns an error and does not call
Agent Core.**

Proceeding would leave `user_id` empty, and the Signals connectors would then issue
`?phone_number=` (400 from the upstream) or write a participant against a malformed
number. This is not hypothetical: the same class of bug was observed on 2026-09-21 in the
blue-dots web flow, where a `null` root `user_id` was rendered as the literal string
`"null"` and sent to `action/perform`, producing a 422.

The failure is surfaced as a spoken fallback utterance (§10), not an HTTP error, so the
caller hears something rather than silence. The rule is the same whether the cause is an
inbound call with no variables or a misconfigured agent prompt.

---

## 8. Streaming

The shim calls **`POST /stream_turn`**.

```
POST /stream_turn
   │
   ├── SignalEvent    → ignored (logging only)
   ├── SentenceEvent  → chunk: delta.content = event.text
   ├── SentenceEvent  → chunk
   └── DoneEvent      → final chunk (finish_reason, usage) then [DONE]
```

`SentenceEvent` carries one trust-checked sentence, emitted as it is produced.
`DoneEvent` is always terminal and carries `was_escalated`, `session_ended`,
`was_tool_used`, `model_used`, `latency_ms`, `error_type`, `error_message`
(`agent_core/src/models.py:286`). This is close to a 1:1 mapping onto chat-completions
streaming.

> **#369 names `POST /process_turn`. That is the wrong endpoint.** `/process_turn` blocks
> until the whole turn completes and returns one finished string, which the shim would
> then have to chop into fake chunks.
>
> Measured turn latency in the blue-dots web flow on 2026-09-21 was **4.0–6.2 seconds**
> (4878 ms, 4070 ms, 4417 ms, 4432 ms, 4605 ms, 6010 ms, 6216 ms). #370 sets a target of
> **800–1200 ms**. With a blocking call the caller hears silence for the entire turn, so
> the target cannot be met regardless of how well the shim is written.
>
> With `/stream_turn`, VoicEra's TTS begins speaking sentence one while the rest is still
> being generated. Time-to-first-audio becomes the time to the first sentence, not the
> whole turn.

Session mode (`POST /sessions/{id}/input` + `GET /sessions/{id}/events`) was also
considered. It streams equally well and adds native barge-in, but requires the shim to
manage a long-lived subscription per call and correlate events back to turns — too many
moving parts for a throwaway service. If barge-in is needed later,
`DELETE /sessions/{session_id}/active_turn` and the `abort_event` hook in
`agent_core/src/base.py` are the extension points.

---

## 9. Call termination

VoicEra ends a call when the LLM emits a tool call. With
`automatic_call_ending.enabled` and `graceful_llm_call_ending` set on the agent, it
appends an `end_conversation` function to the tools it sends
(`apps/runtime/services/pipecat/call_ending.py`):

```python
async def end_conversation(params):
    """End the conversation and shut down the bot."""
    await params.result_callback({"status": "ended"})
    await params.llm.push_frame(EndWorkerFrame(), FrameDirection.DOWNSTREAM)
```

So the translation is:

```
DoneEvent.session_ended == True   ─┐
DoneEvent.was_escalated == True   ─┴─▶  tool_calls chunk: end_conversation
                                        finish_reason: "tool_calls"
```

VoicEra executes it and drops the call. This satisfies #369's *"translate
`was_escalated` into call termination"* with no VoicEra code change — one agent setting.

The shim should check that `end_conversation` is present in the incoming `tools` before
relying on it; if the operator has not enabled graceful call ending, escalation can only
be spoken, not acted on. That condition is worth logging loudly.

---

## 10. Error handling

Every failure becomes a **spoken fallback utterance**, streamed as ordinary content with
`finish_reason: "stop"` — never an HTTP error status, which the caller would experience
as silence or a dropped call.

| Failure | Behaviour |
|---|---|
| `DoneEvent.error_type` set | speak the configured fallback |
| Agent Core unreachable / times out | speak the configured fallback |
| Identity missing or unparseable (§7) | speak the configured fallback; do not call Agent Core |
| Malformed request | 400 — this is an operator misconfiguration, not a caller-facing event |

Fallback wording comes from configuration, not source. Per
`.claude/rules/configuration-discipline.md`, caller-facing text must not be hardcoded.

---

## 11. What the operator configures in VoicEra

All dashboard/configuration; no code changes.

1. LLM provider → `azure_openai`
2. `endpoint` → the shim's base URL
3. `model` → any string (used only as the URL's deployment segment)
4. API key → the shim's configured key
5. Agent prompt → the `[dpg] phone={{phone}} call={{call_id}}` marker line
6. `automatic_call_ending.enabled` + `graceful_llm_call_ending` → on
7. Outbound calls → `custom_variables` per call carrying `phone` and `call_id`

---

## 12. Open questions for the reviewer

1. **Session identity (§7)** — phone for both `user_id` and `session_id`, or
   `session_id` = VoicEra's `call_id`? Recommendation (b), decision deferred.
2. **Inbound calls (§13)** — ask VoicEra to plumb the caller's number through, ship
   outbound-only first, or both in parallel?
3. **Placement (§4)** — extra routes on `reach_layer/web`, or a sibling module?

---

## 13. Risks

### Inbound calls cannot carry the caller's phone number

Think of the system prompt as a letter VoicEra writes before the conversation starts,
with blanks in it (`{{phone}}`). The blanks are filled from a bag of values
(`custom_variables`).

**Outbound** — VoicEra dials out, so it knows the number and hands over the bag when the
call is created:

```
POST /calls/outbound  { to_number: "9900112233",
                        custom_variables: { phone: "9900112233" } }
        ↓
letter reads: "[dpg] phone=9900112233"          reaches the shim ✅
```

**Inbound** — the phone rings. VoicEra records who called, but the registration endpoint
has no `custom_variables` field, so the bag is never created:

```
POST /calls/inbound   { provider_call_sid, from_number: "9900112233", to_number }
                        ↑ the number IS here
                        ↓ but there is nowhere to put it
letter reads: "[dpg] phone="                     blank ✗
```

`substitute_variables` resolves an unknown placeholder to `""`
(`audio.py:17` — `str(variables.get(match.group(1), ""))`), so the marker arrives empty
and the shim fails fast per §7.

This is a plumbing gap, not missing data — VoicEra has the number in `from_number` and
simply never forwards it to where the prompt is filled in. It is a small additive change
on their side. It is made worse on one path: the websocket registration route hardcodes
`from_number="unknown"` (`apps/runtime/routes/agent.py:149`), discarding the number
before it reaches the call log at all.

**Consequence: outbound is shippable today; inbound cannot function until VoicEra
forwards per-call variables (or `from_number`) into the inbound call log.** This does not
block building or testing the shim — the design is identical for both directions, and
#370's mock harness needs no VoicEra at all.

### Service count against the CPU budget

#366 specifies a trimmed **9-service** stack and explicitly drops all reach layers
(VoicEra is the reach layer in that deployment). The shim reintroduces one, making ten.
The analysis already budgets the nine at ~2.5 CPU / 2.5 GB against a confirmed
**2 CPU / 4 GB** allocation. Memory is comfortable; CPU is not, and this adds to it.

### Missing design documents

The three documents the epic treats as the design basis do not exist (§2). Decisions
attributed to "the action plan" — Approach A, the trimmed 9-service set, the CPU budget,
the 800–1200 ms target, the STT/TTS risk rating — have no retrievable source. Two of
those attributed decisions are contradicted by this design (§5 the `base_url` dependency,
§8 the endpoint choice), which suggests the others are also worth re-checking rather
than inherited.

### Throwaway status

#376 replaces the shim with adapter interfaces on both sides. Every feature added here is
migration debt. Anything beyond completing one call should be refused.

---

## 14. Testing (#370)

A mocked OpenAI client shaped like Pipecat's requests. Requires no VoicEra.

| Behaviour | Check |
|---|---|
| Session continuity | Multi-turn: identity extracted once, `session_id` stable, Agent Core sees one session |
| Chunk shape and ordering | Valid `chat.completion.chunk` objects, content in order, terminal `[DONE]` |
| Error paths | Agent Core down, timeout, `DoneEvent.error_type` → fallback utterance, never a bare HTTP error |
| Call termination | `was_escalated` / `session_ended` → `end_conversation` tool call + `finish_reason: "tool_calls"` |
| Missing identity | Absent or malformed marker → fallback, and **no** Agent Core call |
| Azure URL shape | `/openai/deployments/{x}/chat/completions?api-version=y` with an `api-key` header is accepted |
| Latency | Time-to-first-chunk recorded against the 800–1200 ms target, reported separately from whole-turn latency |

Time-to-first-chunk is the number that matters, and it is the one #370's "latency per
turn" wording obscures. Both should be recorded.

---

## 15. Out of scope

- Option B / #376 — the `agent_core` provider in VoicEra's registry
- Barge-in and `cancel_turn`
- Recordings reconciliation, campaign ownership, OTel bridging
- Multi-tenancy (`org_id`); sessions are identified by `session_id`, callers by `user_id`
- Any use of VoicEra's dashboard, campaigns or knowledge base as product capabilities
