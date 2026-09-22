# reach_layer/bridge

An OpenAI-compatible `/v1/chat/completions` endpoint that sits in front of
Agent Core. It exists so that any client speaking the OpenAI chat-completions
wire format — in particular Voicera-style telephony bridges that expect an
"LLM" endpoint — can drive a full DPG conversation (Agent Core → Knowledge
Engine / Action Gateway / Trust Layer / Memory Layer) without knowing any of
that machinery exists. From the caller's side this looks like any other
OpenAI-compatible chat model; from Agent Core's side it is just another Reach
Layer channel (`channel: "bridge"`), alongside `cli`, `web`, `voice`, `mcp`.

The bridge is request-driven only: it has no input source to poll (no
websocket, no queue), so it does nothing but serve HTTP.

## Endpoint

```
POST /v1/chat/completions
GET  /health
```

`/health` returns `{"status": "ok"}` unconditionally — it reports process
liveness, not Agent Core's health. If Agent Core is down or rejects the
channel, `/health` still returns 200 and the failure only shows up on the
next `/v1/chat/completions` call (as a `502 api_error`).

Only the last `user`-role message in `messages` is sent to Agent Core as the
caller's utterance — the contract assumes the client sends just the newest
turn, not a replayed transcript, but a client that sends more still works
because the newest `user` message is what's picked out.

## `metadata.caller_phone` — required, and wrong format fails silently

The chat-completions contract has no field for caller identity. This channel
requires one anyway, because Signals DPG keys profile lookup, profile writes,
and job applications on the phone number — it is the job-seeker's identity,
not an attribute stored next to one. Every request must carry it:

```json
{"metadata": {"caller_phone": "919900112233"}}
```

**Format:** digits only, country code first, no `+`, no spaces, no
punctuation. Length 11–15 digits (an Indian mobile is 10 digits, so 11 is the
shortest value that can carry a country code; 15 is E.164's maximum).
`919900112233` is valid; `9900112233` (missing country code, 10 digits),
`+919900112233` (leading `+`), and `99 001 122 33` (spaces) are all rejected
with `400 invalid_request_error` and `param: "metadata.caller_phone"`.

**The dangerous case is the one validation *can't* catch.** The check only
confirms the value is 11–15 plain digits — it has no way to know whether
those digits are the caller's *real* number with the *right* country code.
Send a value that is syntactically fine but wrong in content — a domestic
number with someone else's country code prefixed, a typo'd digit, a test
number — and the request sails through validation, gets treated as a normal
session key, and fails silently *downstream*:

- Signals' profile lookup on that number matches nothing, so every call
  looks like a first-time caller even if the same person rang back a minute
  ago.
- Duplicate profile records accumulate under different bogus numbers for
  what is really one person.
- The profile actually written to Signals holds the wrong number, so no
  employer or recruiter can ever reach the candidate by phone.

There is no server-side way to detect or repair this after the fact — it
looks exactly like a legitimate first-time caller. Whatever system sits
upstream of this endpoint (the telephony bridge / Voicera integration) is the
only place that can guarantee `caller_phone` is the real, correctly
country-coded number before it ever reaches this endpoint.

One more format trap, in the other direction: a leading `+` isn't just
rejected by validation — even if it *weren't* rejected, it would corrupt the
write. Action Gateway renders the identity as `"+{user_id}"` when writing to
Signals, so a caller_phone that itself starts with `+` would produce a
doubled `"++91..."` on write.

## `stream` — true vs false

`stream` follows the OpenAI default: omitted or `false` means non-streaming.

**`stream: false` (default):** the endpoint blocks on Agent Core's
`/process_turn`, then returns one JSON `chat.completion` object with the full
reply in `choices[0].message.content`. `usage` is always
`{"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}` — Agent
Core's turn API doesn't return token counts to Reach Layer, so this channel
cannot report them either. Consumers that gate billing or context-window
logic on `usage` should not rely on this field being real.

**`stream: true`:** the endpoint calls Agent Core's `/stream_turn` (SSE) and
re-emits it as a standard OpenAI `chat.completion.chunk` stream: an opening
chunk with an empty-content delta and `role: "assistant"`, one or more
content-delta chunks (Agent Core's internal "signal" progress events are
filtered out — only `sentence` events become chunks), a final chunk carrying
`finish_reason: "stop"` (and, if the domain's `terminal_word` config is set
and the turn ends the session, that word appended to the last piece of
content), and a closing `data: [DONE]` line. Set
`stream_options: {"include_usage": true}` to also get a trailing chunk
carrying the same all-zero `usage` object described above.

If the client disconnects mid-stream, the in-flight Agent Core turn is
cancelled (best-effort) rather than left to run to completion unobserved —
important because an unobserved turn that was mid-way through submitting a
profile or a job application would otherwise act on a sentence the caller
never finished.

## No authentication

There is no API key, bearer token, or any other credential check on this
endpoint — anyone who can reach it can drive a full conversation, including
whatever writes the conversation makes to Signals. This is deliberate: the
only access control is that the service must not be reachable from outside
the cluster/host network. It is safe **only** as long as the deployment
enforces that boundary (network policy / firewall / no public ingress, same
posture as `agent_core:8000` itself). Do not expose this port publicly
without adding authentication first — nothing in this channel's code will
stop you.

## `channels.bridge` config

Loaded the same way every other Reach Layer channel is: framework defaults
from `reach_layer/config/dpg.yaml` deep-merged with the domain override at
`$CONFIG_FOLDER/domain.yaml` (or `reach_layer/config/domain.yaml` when
`CONFIG_FOLDER` isn't set), scoped to `reach_layer.channels.bridge`.

```yaml
reach_layer:
  channels:
    bridge:
      agent_core_url: "http://agent_core:8000"   # default; override per deploy
      terminal_word: "Thank you"                  # spoken/written just before
                                                    # the caller's client closes
                                                    # the call — the shim never
                                                    # ends the call itself
      timeout_s: 60.0                              # per-turn HTTP timeout to
                                                    # Agent Core
      server:
        host: "0.0.0.0"
        port: 8008
```

All keys are optional with the defaults shown above (`main.py`'s
`_load_config`). `agent_core_url`'s repo default (`http://agent_core:8000`)
is a Docker Compose service hostname — running the bridge as a bare host
process (outside `docker compose`) requires overriding it, e.g.
`http://localhost:8000`, in the domain config actually loaded.

`terminal_word` is domain-specific copy (e.g. blue-dots uses `"Thank you"`)
and is the *only* signal the caller's client gets that the conversation is
over — the bridge itself never terminates the call.

## curl examples

Non-streaming:

```bash
curl -s -X POST http://localhost:8008/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"any","stream":false,
       "metadata":{"caller_phone":"919900112233"},
       "messages":[{"role":"user","content":"hello"}]}' | python3 -m json.tool
```

Streaming:

```bash
curl -sN -X POST http://localhost:8008/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"any","stream":true,
       "metadata":{"caller_phone":"919900112233"},
       "messages":[{"role":"user","content":"hello"}]}'
```

Streaming with a trailing usage chunk:

```bash
curl -sN -X POST http://localhost:8008/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"any","stream":true,"stream_options":{"include_usage":true},
       "metadata":{"caller_phone":"919900112233"},
       "messages":[{"role":"user","content":"hello"}]}'
```

## Verification status

Verified end to end against the running local stack on the `blue-dots`
domain (dev Signals cluster): `/health` on 8008, both `stream` modes against
a live Agent Core, `channel=bridge` confirmed in force in Agent Core's logs
(no `Unsupported channel` error), and multiple full conversational journeys
(consent → trade/location → job search → profile → apply) each producing a
`live` Signals profile item (`POST /api/v1/admin/participant` → 200,
confirmed via `GET .../admin/participant?phone_number=...`) carrying the
values the caller actually gave.

The final "submit application" step
(`POST /api/v1/action/perform`) is where Agent Core's tool-calling LLM must
carry forward the `profile_item_id` / `acting_as_user_id` it received from an
earlier tool result into the `apply_job` tool call. This is documented
upstream as an intermittent, LLM-driven limitation, not a defect in this
channel: on failure the LLM sometimes supplies a placeholder instead of the
real id (Action Gateway then rejects it with `400 Invalid UUID`, which the
bridge surfaces faithfully as a normal turn response, not a crash) or, on at
least one observed run, narrates a success message without calling the tool
at all. **Do not trust the assistant's own "submitted" reply as evidence** —
confirm independently via the Action Gateway log
(`~/.config/kkb/ai-diffusion-local/logs/action_gateway.log`, look for
`tool=apply_job` and its HTTP status) and, ideally, the resulting Signals
action record.
