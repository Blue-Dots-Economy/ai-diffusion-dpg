# VoicEra integration — what we need from the VoicEra side

**Purpose:** everything the integration needs from VoicEra, for discussion and sign-off.
**Date:** 2026-09-22

---

## Context

We are building a small service that implements OpenAI's `POST /v1/chat/completions` and
is backed by our agent platform. VoicEra points its LLM provider at our URL and otherwise
behaves exactly as it does today — same request shape, same streaming, same response
parsing.

STT, TTS, VAD, turn-taking, transport and telephony all remain entirely VoicEra's. We are
not asking for a custom provider, a plugin, or any change to the pipeline.

The items below are what the integration needs in order to work.

---

## 1. Send the caller's phone number on every request · blocking

In our jobs domain the phone number **is** the user's identity. Every downstream operation
is keyed on it: checking whether the caller already has a profile, creating or updating
their record, and submitting the job application on their behalf. Without it the demo
cannot perform its core function, and any attempt would write records against an empty or
malformed number.

This is the one place where "point at us and change nothing" does not hold. A standard
OpenAI request has no field for a caller's phone number and no reason to have one, so
unless VoicEra sends it deliberately we receive nothing that identifies the caller. We
also cannot obtain it during the conversation: on a phone call the platform already knows
who dialled, and the agent is specifically instructed never to ask a caller for their own
number.

**Please pick whichever of these is easiest on your side.**

| | Where | Note |
|---|---|---|
| **1** | `metadata` on the request body | A standard OpenAI field — 16 key-value pairs, values up to 512 characters. Designed for exactly this, and can carry item 3 in the same place. `{"caller_phone": "919900112233", "call_id": "..."}` |
| **2** | An HTTP header, e.g. `X-Caller-Phone` | Outside the request body entirely. Requires your LLM configuration to support custom headers on outbound calls — please confirm whether it does. |
| **3** | The `user` field | A plain string, documented as "a stable identifier for your end-users". Deprecated but still accepted. |
| **4** | `safety_identifier` | Works — **but** its documentation instructs implementers to *hash* the value to avoid transmitting identifying information. If this option is used we need the **raw** number. A hash is unusable for us. |

A custom message role such as `{"role": "contact", ...}` will not work: the role enum is
closed, so a conformant SDK rejects it before the request is sent.

**Format:** country code first, digits only, no `+` and no spaces — e.g.
`919900112233`. Our records are keyed on the account phone, which carries the country
code.

## 2. Send only the newest user message, not the whole conversation

OpenAI is stateless, so clients normally resend the entire conversation on every request.
Our platform is the opposite: it holds the conversation history, the journey state and the
collected profile fields itself, and its API takes a single new message.

Please send only the latest user utterance.

## 3. Send a per-call identifier alongside the phone number

Our platform needs to know which conversation a request belongs to, and chat-completions
carries no session concept. The phone number identifies the *person*, not the *call* — so
without a per-call id, a caller who rings back resumes their previous conversation
mid-flow, and the agent picks up at "what's your age?" instead of greeting them.

Please send whatever per-call identifier VoicEra already generates, alongside the phone
number. If `metadata` is used for item 1, this costs nothing extra.

## 4. Point the LLM provider at our base URL

This is how VoicEra reaches us at all.

Please confirm whether your OpenAI provider configuration accepts a custom `base_url`. If
it does not, we understand the Azure provider exposes a settable `endpoint` — we can serve
that URL shape instead, which needs no code change on your side. Either works; we need to
know which so we build the right surface.

---

## What we are not asking for

- **No change to your pipeline.** STT, TTS, VAD, turn-taking, transport and telephony
  remain entirely yours.
- **No change to how you call an LLM.** Same request shape, same streaming, same response
  parsing.
- **No custom provider or plugin** in your registry.
- **No changes to your dashboard, campaigns or knowledge base.**

---

## Priority

If discussion time is short, these two need settling first:

1. **Item 1** — the caller's phone number. Nothing works without it.
2. **Item 4** — how we get pointed at: `base_url` or the Azure URL shape.
