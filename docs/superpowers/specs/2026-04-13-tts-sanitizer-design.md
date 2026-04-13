# TTS Text Sanitizer — Implementation Design

## Goal

Prevent the Raya TTS model from reading markdown symbols and emojis literally by inserting a semantic sanitizer stage into the Pipecat pipeline.

## Problem

Agent Core LLM responses contain markdown formatting (`**bold**`, `*italic*`, `## headers`, bullet lists, code blocks, emojis). The Raya TTS model receives these as raw text and pronounces the markers literally (e.g., "asterisk asterisk sigh asterisk asterisk"). The same issue applies to any `TTSSpeakFrame` text — including the configured greeting and fallback phrase — so the fix must cover all sources, not just Agent Core responses.

## Architecture

A `TTSTextSanitizerProcessor` is inserted into the Pipecat pipeline between `AgentCoreLLMProcessor` and `RayaTTSService`. It intercepts every `TTSSpeakFrame`, applies semantic transformations to produce spoken-language-appropriate plain text, and re-emits a new `TTSSpeakFrame` with the cleaned text. All other frame types pass through unchanged.

```
transport.input
  → VADProcessor
  → RayaSTTService
  → AgentCoreLLMProcessor       ← pushes TTSSpeakFrame with raw LLM text
  → TTSTextSanitizerProcessor   ← intercepts, sanitizes, re-emits
  → RayaTTSService              ← always receives plain text
  → transport.output
```

The processor takes no constructor arguments and has no config dependency.

## Sanitization Rules

Transformations are applied in order. Each rule is semantic — it preserves the meaning the LLM intended rather than simply discarding markers.

| Construct | Transformation | Rationale |
|---|---|---|
| `**text**`, `*text*`, `__text__`, `_text_`, `***text***` | `text` | Emphasis is conveyed by the words; TTS handles it naturally |
| Single or multiple `\n` newlines | `. ` (period + space) | Newline signals a pause; a period produces a natural TTS breath |
| `## Heading text` | `Heading text. ` | Header text is meaningful; read as a sentence with a following pause |
| Ordered list: `1. item\n2. item\n3+. item` | `First, item. Next, item. Next, item.` | First item prefixed with "First,"; all subsequent items with "Next," — works at any list length |
| Unordered list: `- item` / `* item` | `item. ` | Items read sequentially with a pause between; no ordinal prefix |
| `` `inline code` `` | `inline code` | Inner text is meaningful (command names, values) |
| ```` ```...code block...``` ```` | _(omitted entirely)_ | Code syntax is not meaningful in voice |
| `[label](url)` | `label` | URL is never speakable; label always is |
| `---` / `***` / `___` horizontal rules | `. ` | Section break → speech pause |
| Emoji codepoints | _(removed)_ | No spoken equivalent |
| Multiple consecutive spaces | single space | Whitespace normalisation |
| Leading/trailing whitespace | stripped | Cleanup |

## Files

| Action | Path |
|---|---|
| Create | `telephony_adapter/src/pipecat_services/tts_sanitizer.py` |
| Modify | `telephony_adapter/src/vobiz_adapter.py` |
| Revert | `telephony_adapter/src/pipecat_services/agent_core_llm.py` |
| Create | `telephony_adapter/tests/pipecat_services/test_tts_sanitizer.py` |

### `tts_sanitizer.py`

`TTSTextSanitizerProcessor(FrameProcessor)` — one public method (`process_frame`) and one private method (`_sanitize(text: str) -> str`). `_sanitize` is a pure function with no side-effects.

### `vobiz_adapter.py`

Add `TTSTextSanitizerProcessor()` between `agent` and `tts` in the `Pipeline([...])` constructor call.

### `agent_core_llm.py` (revert)

Remove the `_sanitize_for_tts` function, `_EMOJI_RE` constant, and `import re` added in the interrupted implementation. Revert `TTSSpeakFrame` creation back to `TTSSpeakFrame(text=response_text)`.

## Testing

`test_tts_sanitizer.py` covers:

- Each transformation rule individually (one test per rule)
- Ordered list with >3 items (verifies "First" + "Next" pattern)
- Input with multiple constructs combined (e.g., bold inside a list item)
- Empty string input → empty string out
- `process_frame` with `TTSSpeakFrame` → sanitized text in re-emitted frame
- `process_frame` with a non-`TTSSpeakFrame` → frame passed through unchanged
