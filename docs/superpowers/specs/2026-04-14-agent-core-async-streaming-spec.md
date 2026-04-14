# Agent Core: Async Orchestrator + SSE Streaming — Spec

**Status:** Approved for implementation
**Date:** 2026-04-14
**Issue:** [#71](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/71)
**Sub-tasks:** [#74](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/74) [#75](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/75) [#76](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/76) [#77](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/77) [#78](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/78)
**Depends on:** None — standalone change.
**Blocks:** TurnAssembler spec (#72), Reach Layer restructuring spec (#73)

---

## Problem

Agent Core's `process_turn()` is fully synchronous. For voice channels, end-to-end latency from VAD end-of-speech to first TTS audio is 3–6 seconds. The 2s target requires sentence-by-sentence streaming so TTS can begin sentence 1 while the LLM is still generating sentence 2.

Additionally, there is no mid-turn visibility — callers and UIs receive no signal for what Agent Core is doing (reading memory, checking trust, invoking tools, etc.).

---

## Architecture

### Async migration

`process_turn()` becomes `async def process_turn()`. All 13 steps in the orchestration pipeline become awaitable. All HTTP clients (`memory_layer`, `trust_layer`, `knowledge_engine`, `action_gateway`, `observability_layer`) are migrated to `async def` throughout.

The existing synchronous `POST /process_turn` endpoint wraps the async engine via `asyncio.run()` — its interface and behaviour are unchanged.

All interface ABCs in `agent_core/src/interfaces/` are updated to `async def` signatures to match.

### Event model

Two categories of events are introduced. Both are yielded by `stream_turn()` and serialised to SSE by the streaming endpoint.

**SignalEvent** — pipeline stage notification. No trust check applied.

```python
@dataclass
class SignalEvent:
    type: str = "signal"
    stage: str = ""    # see valid stages below
    status: str = ""   # "start" | "complete" | "skipped"
    detail: str = ""   # optional human-readable info
```

Valid stages: `memory_read`, `trust_input`, `nlu`, `routing`, `ke_retrieval`, `tool_start`, `tool_end`, `trust_output`, `memory_write`

Each stage emits two events: `status="start"` before the operation and `status="complete"` (or `"skipped"`) after.

**SentenceEvent** — one trust-checked sentence from the LLM response.

```python
@dataclass
class SentenceEvent:
    type: str = "sentence"
    text: str = ""
    sentence_index: int = 0
```

**DoneEvent** — terminal event. Always the last event in a stream.

```python
@dataclass
class DoneEvent:
    type: str = "done"
    was_escalated: bool = False
    was_tool_used: bool = False
    model_used: str = ""
    latency_ms: int = 0
    turn_id: str = ""
    turn_status: str = "completed"  # "completed" | "interrupted" | "abandoned"

StreamEvent = SignalEvent | SentenceEvent | DoneEvent
```

Each event serialises to `data: <json>\n\n` for SSE delivery.

### stream_call() — LLM wrapper

Add to `ClaudeLLMWrapper` and `LLMWrapperBase`:

```python
async def stream_call(
    self,
    messages: list[dict],
    tools: list[dict] | None = None,
    system: str | None = None,
    model_override: str | None = None,
) -> AsyncGenerator[str, None]
```

Uses `anthropic.messages.stream()`. Same retry + fallback model logic as `call()`. Same timeout enforcement and structured logging (emitted on stream close with token counts).

If `tool_use` stop reason is detected mid-stream: collect accumulated tool call blocks, raise `ToolUseRequested(tool_calls)` so `stream_turn()` can handle the tool loop and resume.

### stream_turn() — orchestrator

```python
async def stream_turn(self, turn_input: TurnInput) -> AsyncGenerator[StreamEvent, None]
```

Runs the same 13-step pipeline as `process_turn()` but yields events as it progresses:

**Steps 1–7 (pre-LLM):** yield `SignalEvent(stage=..., status="start")` before and `SignalEvent(stage=..., status="complete")` after each stage.

**Step 8 (LLM streaming):** call `llm.stream_call()`. Accumulate tokens into sentences by splitting on `.`, `?`, `!`, `।` (Devanagari danda U+0964), `?` (full-width). For each complete sentence:
1. Call `trust.check_output(sentence_text)`
2. If allowed → yield `SentenceEvent(text=sentence_text, sentence_index=n)`
3. If blocked → yield `SentenceEvent(text=fallback_phrase, sentence_index=n)`, set `was_escalated=True`

Trust check applies **only** to `SentenceEvent` (LLM output). `SignalEvent`s bypass trust entirely.

**Step 9 (tool use):** if `ToolUseRequested` is raised: yield `SignalEvent(stage="tool_start")`, execute via Action Gateway, yield `SignalEvent(stage="tool_end")`, resume LLM streaming with tool result.

**Final:** yield `DoneEvent` with aggregated metadata.

Steps 12–13 (async memory write + observability emit) fire after `DoneEvent` is yielded. If `stream_turn()` is cancelled before the memory write step, the async write task is also cancelled — no stale writes occur.

### POST /process_turn/stream endpoint

```python
@app.post("/process_turn/stream")
async def process_turn_stream(request: ProcessTurnRequest) -> StreamingResponse:
    async def event_generator():
        try:
            async for event in agent_core.stream_turn(turn_input):
                yield event.to_sse()
        except Exception as e:
            yield DoneEvent(turn_status="abandoned").to_sse()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

On unhandled exception: emit `DoneEvent(turn_status="abandoned")` before closing — never leave the client hanging. Structured log on stream open and close (with total `latency_ms` and event count).

**This endpoint is for dev tooling and direct testing only. No reach layer calls it.**

---

## Files changed

| File | Change |
|---|---|
| `agent_core/src/orchestrator.py` | `process_turn()` → `async def`; add `stream_turn()` |
| `agent_core/src/llm_wrapper/base.py` | Add `stream_call()` abstract method |
| `agent_core/src/llm_wrapper/claude_wrapper.py` | Implement `stream_call()` with `anthropic.messages.stream()` |
| `agent_core/src/models.py` | Add `SignalEvent`, `SentenceEvent`, `DoneEvent`, `StreamEvent`, `ToolUseRequested` |
| `agent_core/src/http_clients/*.py` | All methods → `async def` |
| `agent_core/src/interfaces/*.py` | All method signatures → `async def` |
| `agent_core/src/servers/orchestration_server.py` | Add `POST /process_turn/stream`; sync endpoint wraps via `asyncio.run()` |

---

## Key constraints

- `POST /process_turn` (sync) interface and behaviour unchanged.
- Trust check under streaming: per sentence, not batched. Batching reintroduces the latency being eliminated.
- Trust infra failure during streaming: treat as "allow", log error, never block the stream.
- Sentence splitter handles ASCII and Devanagari boundaries.
- Coverage ≥ 70% maintained across `agent_core/`.

---

## Execution order

`#74` (async migration) must merge before `#75`, `#76`, `#77`, `#78` can begin. The remaining four are independent of each other.
