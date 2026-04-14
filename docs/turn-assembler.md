

## The core problem: when is a "turn" done?

Real user input is a stream, not a packet. Your architecture needs to separate:

1. **Input collection** — receiving raw signals from a channel
2. **Turn assembly** — deciding when that stream constitutes a complete intent
3. **Agent invocation** — actually running the model
4. **Response delivery** — streaming output back, possibly cancellable

These are four distinct responsibilities, and conflating any two of them is what causes the problems you're describing.

---

## Input buffer design per channel

**Chat** is the simpler case. Users send fragmented messages like:
> "hey so I wanted to ask about..."
> "actually never mind the previous thing"
> "can you help me book a flight to NYC"

The buffer should collect fragments and apply a **silence-based flush** — typically 0.8–2 seconds of no new messages. You configure this per-context: a power user in a technical tool gets a shorter gap; a consumer chat product gets longer. You also want a **max fragment count ceiling** (e.g. 5 messages) to prevent the buffer from holding forever if the user types slowly.

**VoIP** is harder. You're dealing with ASR output, which means:
- *Partial transcripts* arrive continuously as the model updates its hypothesis
- *End-of-turn* comes from VAD (voice activity detection) — a configurable silence threshold after speech stops
- *Retractions* happen when ASR revises earlier text or when the user says "actually wait"
- *Barge-in* (interruption) happens while *your agent is still speaking*

For VoIP, your buffer should track the current "committed" transcript vs. the "tentative" hypothesis. When VAD fires, you commit and gate invocation.

---

## The turn assembler / invocation gate

This is the critical component most designs skip. It sits between the buffer and the agent and answers: *is this assembled turn ready to invoke?*

Four policies you want to support, all configurable:

**Silence trigger** — invoke after N ms of silence post-last-input. The simplest policy; works for most cases.

**Semantic completeness gate** — run a cheap classifier (small model, or even a regex/grammar heuristic) on the assembled text to decide if it expresses a complete intent. Useful if your users routinely type in very short bursts that together form one question. The tradeoff is latency vs. false-early-invocation.

**Speculative execution** — start a "warm" agent run on the partial turn, but don't commit its output yet. If the turn closes without changes, you get the response faster. If the user adds more, you cancel and restart. This only pays off when LLM latency dominates and input completion is predictable.

**Max wait ceiling** — always force-invoke after an upper bound (e.g. 8s) regardless of the other policies. Prevents the agent from starving indefinitely if the user is typing very slowly or VAD is misfiring.

---

## Handling interruptions and retractions

**Barge-in** (VoIP): when the user starts speaking while the agent is playing back TTS, you need to:
1. Immediately cut the TTS audio stream
2. Cancel the in-flight agent response (if still streaming)
3. Send an interrupt signal back to the buffer to start a new turn

This requires your response stream to be **cancellable mid-generation**. If you're calling an LLM via streaming, you close the connection; if it's TTS, you halt the audio queue. The conversation state needs to record that the previous turn was interrupted (partial), not completed.

**Retractions / self-corrections** (both channels): "actually forget what I said, I meant X" should ideally be collapsed into the turn *before* invocation — this is the buffer's job. After invocation, treat it as a new turn that starts with the agent's understanding that the prior message is to be revised.

---

## Conversation state: what to persist between invocations

Since each invocation is stateless, your state store needs to carry:
- Full message history (compressed/summarized after a depth threshold)
- The **channel context** (is this VoIP or chat — affects response length and style)
- Any **partial turn** that was interrupted (so the agent can acknowledge it)
- A **turn status** flag: `completed`, `interrupted`, `abandoned`

---

## Response delivery differences by channel

**Chat**: you can stream tokens directly into a typing indicator → message reveal pattern. Users tolerate 1–3s of "thinking" time.

**VoIP**: you need sentence-boundary buffering before handing off to TTS. You can't speak half a sentence. So your streaming output should flush at `.`, `?`, `!` boundaries — giving you the latency benefit of streaming while producing speakable chunks. The first sentence should be optimized for speed since perceived latency on a call is brutal.

---

The most common design mistake is making the invocation trigger synchronous with message receipt, which forces you to either invoke too early (bad for fragmented chat) or add ad-hoc delays that accumulate. Building the gate as an explicit, configurable component lets you tune per-channel, per-user-tier, and even per-conversation-phase without touching agent logic.