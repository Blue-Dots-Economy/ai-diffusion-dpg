# Configurable identity and human handoff for the voice agent

**Status:** Draft for review, 2026-10-02.
**Branch:** `spec/identity-handoff`. Implementation lands after `fix/voice-turn-defects`, because it reuses that branch's D3 confirm-then-end path: the `close_confirm` pending and `close_return_to`. The existing `closing_offer` pending has the opposite polarity: there, "नहीं" ends the call.
**Why now:** voice-bench (PR #435) found two problems:
- TC12 (AI disclosure) fails whenever the caller asks for a human.
- Blue Dots has no honest path for "let me talk to a person".

Root-cause notes are in `local_docs/2026-10-02-voice-defects-root-causes.md` §D4, outside the repo.

## 1. Problem

1. **Identity is free text with one hard-coded rule.**
   - The persona prose in `agent_system_prompt` names the assistant.
   - A single style rule answers only "are you a person or a computer?" with "जी, मैं एक AI असिस्टेंट हूँ".
   - Other identity questions ("आप कौन हैं?", "किससे बात कर रहा हूँ?") and requests for a person get no rule.
   - Every use case has to rewrite this prose.
2. **"I want a human" goes nowhere.**
   - NLU already labels it `counsellor_request`, but that label only becomes an observability signal (`signal_intents`). No routing rule uses it.
   - Agent Core's `hitl` block is a canned "a counsellor will call within 24 hours" reply that no Blue Dots phase uses. So the bot either improvises or would promise a callback nobody arranged.
3. **The handoff sink exists, but it is a stub.**
   - Trust Layer has `HiTLBlock.escalate` and `POST /escalate`.
   - Agent Core has `escalate()` clients, but nothing calls them.
   - The only queue backend is `log`. Any other backend still returns `queued=True`, so a caller could be told "passed on" when nothing was delivered (`trust_layer/src/blocks/hitl.py`, `TODO(GH-hitl)`).

The greeting is out of scope: VoicERA sets and speaks it.

## 2. Goals and non-goals

**Goals**
- **G1. Identity from config.** Each use case declares who the assistant is. When the caller asks who they are talking to, whether it is a human, or asks for a person, the assistant answers truthfully from that config.
- **G2. A real handoff.** When the use case allows it, a request for a person sends the caller's details and a call summary to a configured external system. The bot says so only when delivery succeeded.
- **G3. The caller keeps control.** After a handoff, the bot offers to help with anything else or to end the call. Either works.
- **G4. Nothing changes until it is configured.** By default there is no webhook URL and `human_handoff: none`, so today's behaviour holds apart from the honest identity answer.

**Non-goals (v1)**
- Live call transfer through VoicERA.
- A Signals record for the request.
- SMS or email through notification-service.

Any of these can sit behind the webhook later.

## 3. Identity

### 3.1 Config

The new `identity` block lives in the domain half of `agent_core.yaml`:

```yaml
identity:
  name: "ब्लू डॉट्स सहायक"          # spoken name
  kind: ai_assistant               # ai_assistant (only value in v1)
  operator: "Blue Dots"            # who runs the service
  disclosure: "जी, मैं ब्लू डॉट्स की AI सहायक हूँ।"
  human_handoff: none              # none | request
  no_handoff_line: "अभी इस कॉल पर कोई इंसान उपलब्ध नहीं है — मैं ही आपकी मदद कर सकती हूँ।"
```

- All fields except `human_handoff` are required when the block is present. `human_handoff` defaults to `none`.
- **Back-compat:** if the block is absent, no `<identity>` is rendered and the existing persona prose applies unchanged.

### 3.2 Prompt

`build_system_prompt` renders the block as `<identity>` in tier 1, next to `<persona>`, where it is cacheable. It carries one rule:

> When the caller asks who you are or who they are talking to, asks whether you are a human or a computer, or asks to speak to a person: say `disclosure` in one sentence. If `human_handoff` is `none`, say `no_handoff_line`. Then return to the open question. Never claim to be human, and never promise a callback or a person unless the handoff phase reports success.

The Blue Dots persona prose and the hard-coded "computer or person?" rule move into this block. The name moves too: it appears only in `identity`.

## 4. Handoff flow

### 4.1 Trigger

- `counsellor_request` becomes a routable intent, `human_request`, through `act_intents` / `signal_intents`. It keeps emitting `escalation_signal`, as it does today.
- A global routing rule sends `human_request` to the `handoff` phase when `identity.human_handoff == request`.
- With `none`, routing does not change, and the `<identity>` rule produces the honest answer.

### 4.2 The `handoff` phase

This is a deterministic, non-terminal phase: a fixed line chosen by code and no LLM call. It is like the `consent_declined` opening-phrase pattern, but the session continues.

1. **On entry,** if `handoff_status` is unset in the session, Agent Core calls `trust.escalate(...)` (§5) and stores `handoff_status` (`delivered` or `failed`) and `handoff_ticket_id`.
2. **It speaks one fixed line from config:**
   - `delivered`: "मैंने आपकी बात टीम तक पहुँचा दी है, वे आपसे संपर्क करेंगे। क्या मैं और कुछ मदद करूँ, या कॉल यहीं ख़त्म करूँ?"
   - `failed`: "अभी मैं आपकी बात आगे नहीं भेज पाई — मैं ही आपकी मदद कर सकती हूँ। आप क्या जानना चाहते हैं?"
   - `already`, on a second request in the same call: "आपकी बात पहले ही टीम तक पहुँच चुकी है।" No second escalation is sent.
3. **The `delivered` line declares `pending: close_confirm`,** reusing D3's confirm-then-end path. On entry the phase also records `close_return_to`, which is the same as `handoff_return_to`:
   - "हाँ, ख़त्म करो" ends the call through the existing termination path.
   - Anything else returns the caller to the phase they were in before the handoff (`handoff_return_to`, stored on entry).
4. **`failed` and `already`** return to the previous phase on the next turn, with no pending.

All three lines are config (`handoff.lines.{delivered,failed,already}`), so the LLM never phrases the promise.

## 5. Trust Layer: delivery

### 5.1 Request

`POST /escalate` is extended in a backward-compatible way:
- `HiTLEscalateRequest` gains an optional `handoff: dict` payload.
- `HiTLEscalateResponse` gains `delivered: bool`.
- The existing `queued` and `ticket_id` fields are unchanged.

### 5.2 Payload

Agent Core builds the payload from session state only. Nothing in it is free-form LLM output:

```json
{
  "ticket_id": "TKT-…",
  "use_case": "blue-dots",
  "reason": "human_request",
  "created_at": "2026-10-02T12:00:00Z",
  "caller": { "phone": "91…", "name": "…", "language": "hi" },
  "context": { "step": "job_match", "last_caller_turn": "…",
               "trade": "…", "location": "…",
               "applications": [ { "job_item_id": "…", "status": "submitted" } ] },
  "summary": [ { "caller": "…", "bot": "…" } ],
  "call_id": "…"
}
```

`summary` holds the last N exchanges (`handoff.summary_turns`, default 6), each capped at 300 characters.

### 5.3 Webhook backend

This replaces the `TODO(GH-hitl)` webhook branch.
- **Selection:** `trust.hitl.queue_backend: webhook`.
- **URL:** from env `HITL_WEBHOOK_URL`, not YAML, so each deployment sets its own. A missing URL or a non-HTTPS URL gives `delivered=false, reason=misconfigured`. Plain HTTP is allowed only when `HITL_WEBHOOK_ALLOW_HTTP=1`, for local tests.
- **Signing:** an HMAC-SHA256 signature of the raw body, sent as the header `X-Handoff-Signature: sha256=<hex>`. The secret comes from env `HITL_WEBHOOK_SECRET`. Without a secret, nothing is sent and the result is `delivered=false, reason=misconfigured`.
- **Timeout and retry:** a 3 s timeout, with one retry on a timeout or 5xx.
- **Result:** a 2xx response gives `delivered=true`. Anything else gives `delivered=false` with `reason` set to one of `timeout`, `http_<code>` or `error`.
- **`log` backend:** it stays the default, logs as today, and returns `delivered=false, reason=log_only`. The bot then uses the `failed` line, because logging is not a handoff.
- **Unsupported backends** (`redis`, or unknown) return `queued=false, delivered=false`. This fixes the false `queued=True`.

### 5.4 Privacy

- The payload goes only to the webhook.
- Trust and Agent Core logs record `ticket_id`, `delivered`, `reason` and latency, never the payload.
- The webhook secret and URL are never logged.

## 6. Observability

Each handoff emits one event, `handoff`, with:
- `outcome`: `delivered`, `failed` or `already`;
- `reason`;
- `latency_ms`;
- `ticket_id`.

It goes out through the existing `emit_signal` path.

## 7. Config and dev-kit sync

The runtime↔dev-kit rule applies in the same PR. The fields to sync are:
- `agent_core.identity`;
- `agent_core.handoff.{lines, summary_turns}`;
- the `trust.hitl.queue_backend` value `webhook`.

Each must appear in:
- the runtime schemas;
- `dev-kit/dev_kit/schemas/domain/{agent_core,trust_layer}.py`;
- field rules;
- `dev-kit/dev_kit/schema.py`.

The env vars are documented in the deploy env examples. The default `trust_layer.yaml` stays on `log`.

## 8. Testing

- **Trust Layer:**
  - the webhook backend against a local fake server, covering 2xx, non-2xx, timeout with retry, a correct HMAC header, and missing or non-HTTPS URLs;
  - `log` returns `delivered=false`;
  - unsupported backends return `queued=false`;
  - the payload never appears in logs.
- **Agent Core:**
  - `human_request` routes to `handoff` only when the use case allows it;
  - `delivered`, `failed` and `already` lines;
  - one escalation per call;
  - the `close_confirm` follow-up ends the call on yes and returns to `handoff_return_to` otherwise;
  - `<identity>` renders, and is absent when there is no block;
  - the disclosure rule is present.
- **NLU eval:** cases for "इंसान से बात कराओ", "किसी आदमी से बात करनी है", "क्या आप कंप्यूटर हैं?" and "आप कौन हैं?".
- **voice-bench:** T05 and T11 on the implementation branch with `human_handoff: request`, and the webhook pointed at a local receiver run by the harness. Expected: TC12 and TC14 pass, and the receiver gets exactly one signed payload per handoff call.

## 9. Risks

- **NLU over-triggering `human_request`** ("मुझे किसी से पूछना है"). Mitigation: eval cases and a precision check before switching Blue Dots to `request`.
- **Webhook latency adds to the handoff turn.** It is bounded at about 6 s worst case (3 s plus one retry). A spoken status line covers it, as with tool turns.
- **Who operates the webhook receiver** is a Blue Dots product decision. Until one exists, Blue Dots keeps `human_handoff: none`, and callers get the honest no-handoff line.
