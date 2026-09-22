# VoicEra call — what to ask, and what to ask for

**Purpose:** agenda for the integration call. Everything the shim needs from the VoicEra
side, and everything we need to know before implementation starts.
**Design:** `2026-09-21-voicera-llm-shim-design.md`
**Date:** 2026-09-22

---

## The one-line context to open with

We are building a small service that implements OpenAI's `POST /v1/chat/completions` and
is backed by our Agent Core. VoicEra points its LLM provider at us and otherwise behaves
exactly as it does today — same request, same response shape, same streaming. Nothing in
the VoicEra pipeline changes.

There is one place where that "change nothing" story breaks, and it is the most important
item on this list: **A1**.

---

## Part 1 — Asks (things we need VoicEra to do)

### A1 — Send us the caller's phone number on every request · **BLOCKING**

**Why.** In our jobs domain the phone number *is* the user's identity. Every downstream
call is keyed on it: looking up whether the caller already has a profile, creating or
updating their record, and submitting the job application. Without it we cannot do the one
thing the demo exists to do — and worse than failing, we would write records against an
empty or malformed number.

**Why it is not automatic.** A normal OpenAI request has no field for a caller's phone
number, and no reason to — an LLM does not need to know who is on the line. So unless
VoicEra sends it deliberately, we receive nothing that identifies the caller. We also
cannot ask the caller for it: on a phone call the platform already knows who dialled, and
the agent is specifically instructed never to ask.

**Options, in our order of preference.** Any one of these works; we are asking VoicEra
which is easiest on their side.

| | Where | Note |
|---|---|---|
| **1** | `metadata` on the request body | A standard OpenAI field — 16 key-value pairs, values up to 512 chars. Designed for exactly this, and can carry A3 in the same place. `{"caller_phone": "919900112233", "call_id": "..."}` |
| **2** | An HTTP header, e.g. `X-Caller-Phone` | Outside the request body entirely. Depends on whether their LLM config can set custom headers — see Q1. |
| **3** | The `user` field | A plain string, documented as "a stable identifier for your end-users". Deprecated but still accepted. |
| **4** | `safety_identifier` | Works, **but** its documentation tells implementers to *hash* the value to avoid transmitting identifying information. If this option is chosen we need the **raw** number, not a hash — otherwise it is useless to us. |

**Not viable:** a custom message role such as `{"role": "contact", ...}`. The role enum is
closed, so a conformant SDK rejects it before sending.

**Format we need:** country code first, digits only, no `+` and no spaces — e.g.
`919900112233`. Our upstream keys on the account phone, which carries the country code.

### A2 — Send only the newest user message, not the whole conversation · preferred

**Why.** OpenAI is stateless, so clients normally resend the entire conversation every
turn. Our Agent Core is the opposite — it holds the history, the journey state and the
collected profile fields itself, and its API takes a single new message. If VoicEra sends
the full array we would have to discard most of it.

**The ask:** send just the latest user utterance.

**If that is awkward, we can live with it** — we will take the last `user` message and
discard the rest. We would rather not, because it puts the guessing on us, but it is not
blocking. Just tell us which it will be so we build for it.

### A3 — Send a per-call identifier · strongly preferred

**Why.** Our Agent Core needs to know which conversation a request belongs to, and
chat-completions has no session concept. We can fall back to using the phone number as
the key, but then a caller who rings back a second time resumes their previous
conversation mid-flow — the bot picks up at "what's your age?" instead of greeting them.

**The ask:** whatever per-call id VoicEra already generates, sent alongside A1. If
`metadata` is used for A1, this costs nothing extra.

### A4 — Point the LLM provider at our base URL

**Why.** This is how VoicEra reaches us at all.

**What we need to know:** whether their OpenAI provider configuration can take a custom
`base_url`. If it cannot, we understand the Azure provider exposes a settable `endpoint` —
we can serve that URL shape instead and no VoicEra code change is needed. Either is fine;
we just need to know which, so we build the right surface.

### A5 — VAD timing for Indian callers · from the earlier integration plan, not the shim

Their default `stop_secs` of `0.4` cuts callers off mid-sentence. We run `1.0`, because
older and rural callers' inter-word pauses run 0.6–1.0 s and the shorter window turns
those into false turn boundaries. Raising it, or making it configurable per agent, matters
for their own Indic-language use cases as much as ours.

---

## Part 2 — Questions (things we need to know)

### Q1 — Can your LLM configuration set custom HTTP headers on outbound calls?
Decides whether option 2 in A1 is available.

### Q2 — What is your HTTP timeout on LLM calls?
Our turns currently take **4–6 seconds**. If the timeout is below that, requests fail
before we answer. We stream the reply, so the first audio starts well before the turn
finishes — but that only helps if the timeout applies to **time-to-first-byte** rather
than to the whole response. Which is it?

### Q3 — How does a call end today?
**This one matters more than it sounds.** Our design cannot hang up the call — we have no
control over the line, and the only in-band mechanism would be a tool call, which we are
not using. So when the conversation finishes we speak a closing word and stop; the line
stays open until VoicEra ends it.

- Is there an idle or silence timeout that will end the call? How long?
- Is there any other signal we could send that VoicEra would act on to hang up?

If there is no reasonable timeout, a caller who says goodbye sits on an open line, which
would look bad in a demo.

### Q4 — Do you send a `tools` array on LLM requests?
We plan to ignore it. Just confirming there is nothing in there you expect us to act on.

### Q5 — Does your TTS handle markdown and emoji, or read text literally?
There is no text sanitizer in this path on our side. We instruct the model to produce
speech-ready text, but models occasionally disobey. If a `**bold**` marker or an emoji
reaches your TTS, is it stripped or read aloud?

### Q6 — Do you always stream, or do you also make non-streaming calls?
We are implementing both, so either is fine. Knowing which you use tells us where to focus
testing.

### Q7 — Is the TTS voice fixed per agent, or can it change mid-call?
Our agent mirrors the caller's language — it will switch into Hindi or Gujarati if the
caller does. If the voice is fixed at call setup, a Hindi reply may be spoken by a voice
configured for English.

### Q8 — Inbound, outbound, or both for the demo?
It makes no difference to how we build the shim; it affects how A1 gets wired on your
side, since outbound calls know the number up front and inbound ones learn it from the
telephony provider.

### Q9 — Greeting
We understand the first thing the caller hears comes from your agent's
`greeting_message`, not from us — our service only responds once spoken to. Can you
confirm? We will supply the exact wording to put there.

---

## Part 3 — What we are NOT asking for

Worth saying out loud, so the list above does not sound bigger than it is.

- **No change to your pipeline.** STT, TTS, VAD, turn-taking, transport and telephony stay
  entirely yours.
- **No change to how you call an LLM.** Same request shape, same streaming, same response
  parsing.
- **No custom provider or plugin** in your registry. That is the longer-term design; this
  one deliberately avoids it.
- **Nothing on the critical path.** If any ask above needs a release cycle, tell us and we
  will work around it rather than wait.

---

## Priority for the call

If time runs short, these are the ones that actually block us:

1. **A1** — the phone number. Nothing works without it. Agree the mechanism on the call.
2. **A4** — how we get pointed at. `base_url` or the Azure URL shape.
3. **Q3** — how calls end, given we cannot hang up.

Everything else can be settled over email.
