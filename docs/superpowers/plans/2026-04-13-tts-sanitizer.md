# TTS Text Sanitizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a `TTSTextSanitizerProcessor` into the Vobiz pipeline to semantically transform markdown/emoji in all `TTSSpeakFrame` text before it reaches the Raya TTS model.

**Architecture:** A new Pipecat `FrameProcessor` (`tts_sanitizer.py`) sits between `AgentCoreLLMProcessor` and `RayaTTSService`. It intercepts every `TTSSpeakFrame`, applies ordered regex-based transformations (code fences → omit, ordered lists → "First/Next", newlines → pauses, etc.), and re-emits a cleaned frame. All other frame types pass through unchanged. The partial implementation that was accidentally added to `agent_core_llm.py` is reverted first.

**Tech Stack:** Python 3.11, `re` (stdlib only), Pipecat `FrameProcessor`, `TTSSpeakFrame`.

---

## File Structure

| Action | Path | Responsibility |
|---|---|---|
| Revert | `telephony_adapter/src/pipecat_services/agent_core_llm.py` | Remove `_sanitize_for_tts`, `_EMOJI_RE`, `import re`; revert `TTSSpeakFrame` line |
| Create | `telephony_adapter/src/pipecat_services/tts_sanitizer.py` | `TTSTextSanitizerProcessor` + `_sanitize()` pure function |
| Modify | `telephony_adapter/src/vobiz_adapter.py` | Add `TTSTextSanitizerProcessor()` between `agent` and `tts` in pipeline |
| Create | `telephony_adapter/tests/pipecat_services/test_tts_sanitizer.py` | All tests for sanitizer |

---

## Task 1: Revert partial implementation from `agent_core_llm.py`

**Files:**
- Modify: `telephony_adapter/src/pipecat_services/agent_core_llm.py`

The interrupted implementation added `import re`, `_EMOJI_RE`, `_sanitize_for_tts()`, and changed the `TTSSpeakFrame` line. This task reverts all of that so the file is clean for the proper approach.

- [ ] **Step 1: Remove `import re`, `_EMOJI_RE`, and `_sanitize_for_tts` from `agent_core_llm.py`**

The file currently looks like this at the top (lines 1–81):

```python
from __future__ import annotations

import logging
import re          # ← REMOVE this line
import time

import httpx

from pipecat.frames.frames import EndFrame, Frame, TTSSpeakFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

# Matches Unicode emoji ranges (BMP + supplementary planes).
_EMOJI_RE = re.compile(   # ← REMOVE _EMOJI_RE block (lines 28–39)
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)


def _sanitize_for_tts(text: str) -> str:   # ← REMOVE entire function (lines 42–80)
    ...


class AgentCoreLLMProcessor(FrameProcessor):
```

After the edit, the top of the file must look exactly like this:

```python
"""
telephony_adapter/src/pipecat_services/agent_core_llm.py

AgentCoreLLMProcessor — Pipecat FrameProcessor that bridges TranscriptionFrames
to Agent Core's /process_turn HTTP endpoint.

Receives TranscriptionFrame from RayaSTTService, POSTs to Agent Core, then
pushes TTSSpeakFrame downstream so RayaTTSService can synthesize the response.
On was_escalated=True, also pushes EndFrame after the speak frame to close the
pipeline gracefully (VobizFrameSerializer will hang up the call on EndFrame).
On HTTP error or timeout, pushes a TTSSpeakFrame with the configured fallback
phrase so the call continues rather than hanging silently.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import logging
import time

import httpx

from pipecat.frames.frames import EndFrame, Frame, TTSSpeakFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


class AgentCoreLLMProcessor(FrameProcessor):
```

- [ ] **Step 2: Revert `TTSSpeakFrame` line (currently line 217)**

Currently reads:
```python
await self.push_frame(TTSSpeakFrame(text=_sanitize_for_tts(response_text)))
```

Change to:
```python
await self.push_frame(TTSSpeakFrame(text=response_text))
```

- [ ] **Step 3: Verify tests still pass**

```bash
cd telephony_adapter
uv run pytest tests/pipecat_services/test_agent_core_llm.py -v
```

Expected: all tests pass (the sanitizer was not tested in this file).

- [ ] **Step 4: Commit**

```bash
git add telephony_adapter/src/pipecat_services/agent_core_llm.py
git commit -m "revert(agent-core-llm): remove misplaced sanitize_for_tts — moving to TTSTextSanitizerProcessor"
```

---

## Task 2: Create `TTSTextSanitizerProcessor`

**Files:**
- Create: `telephony_adapter/src/pipecat_services/tts_sanitizer.py`
- Create: `telephony_adapter/tests/pipecat_services/test_tts_sanitizer.py`

Build TDD: write all tests first, then implement until they pass.

- [ ] **Step 1: Write the failing tests**

Create `telephony_adapter/tests/pipecat_services/test_tts_sanitizer.py`:

```python
"""Tests for TTSTextSanitizerProcessor and _sanitize()."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from pipecat.frames.frames import TTSSpeakFrame, EndFrame
from pipecat.processors.frame_processor import FrameDirection


# ---------------------------------------------------------------------------
# _sanitize() unit tests — one per rule
# ---------------------------------------------------------------------------

def sanitize(text):
    from src.pipecat_services.tts_sanitizer import _sanitize
    return _sanitize(text)


def test_empty_string_unchanged():
    assert sanitize("") == ""


def test_code_fence_omitted():
    text = "Here is code:\n```python\nprint('hello')\n```\nDone."
    assert sanitize(text) == "Here is code:. Done."


def test_inline_code_inner_text_kept():
    assert sanitize("Use `uv run pytest` to run tests.") == "Use uv run pytest to run tests."


def test_header_becomes_sentence():
    assert sanitize("## Getting Started") == "Getting Started. "


def test_bold_inner_text_kept():
    assert sanitize("This is **important** text.") == "This is important text."


def test_italic_inner_text_kept():
    assert sanitize("This is *emphasized* text.") == "This is emphasized text."


def test_bold_italic_inner_text_kept():
    assert sanitize("This is ***very important*** text.") == "This is very important text."


def test_underscore_bold_inner_text_kept():
    assert sanitize("This is __bold__ text.") == "This is bold text."


def test_underscore_italic_inner_text_kept():
    assert sanitize("This is _italic_ text.") == "This is italic text."


def test_ordered_list_first_next():
    text = "1. Alpha\n2. Beta\n3. Gamma"
    assert sanitize(text) == "First, Alpha. Next, Beta. Next, Gamma."


def test_ordered_list_more_than_three_items():
    text = "1. A\n2. B\n3. C\n4. D\n5. E"
    result = sanitize(text)
    assert result.startswith("First, A.")
    assert result.count("Next,") == 4


def test_unordered_list_dash():
    text = "- Apple\n- Banana\n- Cherry"
    assert sanitize(text) == "Apple. Banana. Cherry."


def test_unordered_list_asterisk():
    text = "* One\n* Two"
    assert sanitize(text) == "One. Two."


def test_link_label_kept_url_removed():
    assert sanitize("See [the docs](https://example.com) for details.") == "See the docs for details."


def test_horizontal_rule_becomes_pause():
    assert sanitize("Section A\n---\nSection B") == "Section A. Section B."


def test_newline_becomes_pause():
    assert sanitize("Hello\nWorld") == "Hello. World."


def test_multiple_newlines_become_single_pause():
    assert sanitize("Hello\n\n\nWorld") == "Hello. World."


def test_emoji_removed():
    assert sanitize("Great job! 🎉") == "Great job!"


def test_multiple_emojis_removed():
    assert sanitize("👋 Hello 🌍") == "Hello"


def test_devanagari_preserved():
    """Hindi text must not be stripped — Devanagari is not emoji."""
    text = "नमस्ते, मैं आपकी मदद कर सकता हूँ।"
    assert sanitize(text) == text


def test_combined_bold_inside_list():
    text = "- **Important**: Do this first\n- Regular item"
    assert sanitize(text) == "Important: Do this first. Regular item."


def test_multiple_spaces_collapsed():
    assert sanitize("Hello   world") == "Hello world"


def test_leading_trailing_whitespace_stripped():
    assert sanitize("  Hello world  ") == "Hello world"


# ---------------------------------------------------------------------------
# TTSTextSanitizerProcessor integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_frame_sanitizes_tts_speak_frame():
    """TTSSpeakFrame text is sanitized and a new TTSSpeakFrame is pushed."""
    from src.pipecat_services.tts_sanitizer import TTSTextSanitizerProcessor

    processor = TTSTextSanitizerProcessor()
    pushed = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    processor.push_frame = capture

    frame = TTSSpeakFrame(text="**Hello** world\n- Item one\n- Item two")
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert isinstance(pushed[0], TTSSpeakFrame)
    assert pushed[0].text == "Hello world. Item one. Item two."


@pytest.mark.asyncio
async def test_process_frame_passes_non_tts_frame_through():
    """Non-TTSSpeakFrame frames pass through unchanged."""
    from src.pipecat_services.tts_sanitizer import TTSTextSanitizerProcessor

    processor = TTSTextSanitizerProcessor()
    pushed = []

    async def capture(frame, direction=None):
        pushed.append((frame, direction))

    processor.push_frame = capture

    frame = EndFrame()
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert len(pushed) == 1
    assert isinstance(pushed[0][0], EndFrame)
    assert pushed[0][1] == FrameDirection.DOWNSTREAM
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
cd telephony_adapter
uv run pytest tests/pipecat_services/test_tts_sanitizer.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` for `src.pipecat_services.tts_sanitizer` — confirms the module does not exist yet.

- [ ] **Step 3: Create `tts_sanitizer.py`**

Create `telephony_adapter/src/pipecat_services/tts_sanitizer.py`:

```python
"""
telephony_adapter/src/pipecat_services/tts_sanitizer.py

TTSTextSanitizerProcessor — Pipecat FrameProcessor that transforms markdown
and emoji in TTSSpeakFrame text into spoken-language-appropriate plain text.

Intercepts every TTSSpeakFrame in the pipeline, applies semantic
transformations (code fences omitted, ordered lists become "First/Next",
newlines become pauses, etc.), and re-emits a new TTSSpeakFrame with the
cleaned text. All other frame types pass through unchanged.

Insert between AgentCoreLLMProcessor and RayaTTSService in the pipeline so
that every TTSSpeakFrame source (Agent Core response, greeting, fallback
phrase) is covered.
Belongs to the Reach Layer / Telephony Adapter block in the DPG framework.
"""
from __future__ import annotations

import re

from pipecat.frames.frames import Frame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Unicode emoji ranges — explicitly excludes Devanagari (U+0900–U+097F) and
# other non-Latin scripts so multilingual text is never corrupted.
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # misc symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002700-\U000027BF"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed chars
    "]+",
    flags=re.UNICODE,
)


def _sanitize(text: str) -> str:
    """Transform markdown and emoji into spoken-language-appropriate plain text.

    Applies transformations in order. Each rule is semantic: it preserves the
    meaning the author intended rather than simply discarding markers.

    Transformation rules (applied in order):
        - Code fences (```...```) → omitted entirely
        - Inline code (`text`) → inner text
        - Headers (## text) → "text. "
        - Ordered lists (1. item\\n2. item) → "First, item. Next, item."
        - Unordered lists (- item, * item) → "item. "
        - Bold/italic (**text**, *text*, __text__, _text_) → inner text
        - Links ([label](url)) → label
        - Horizontal rules (---, ***, ___) → ". "
        - Newlines → ". "
        - Emoji codepoints → removed
        - Multiple spaces → single space
        - Leading/trailing whitespace → stripped

    Devanagari and other non-Latin Unicode scripts are preserved.

    Args:
        text: Raw text that may contain markdown formatting and emoji.

    Returns:
        Plain-text string suitable for speech synthesis. Returns the input
        unchanged if it is empty or None-equivalent.
    """
    if not text:
        return text

    # 1. Code fences — omit entirely (opening fence with optional lang tag + content + closing fence)
    text = re.sub(r"```[^\n]*\n[\s\S]*?```", "", text)
    # Any remaining stray ``` (unclosed fences)
    text = re.sub(r"```[^\n]*", "", text)

    # 2. Inline code — keep inner text
    text = re.sub(r"`([^`]*)`", r"\1", text)

    # 3. Headers — keep text, add pause
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1. ", text, flags=re.MULTILINE)

    # 4. Horizontal rules — before list processing to avoid false matches
    text = re.sub(r"^\s*([-*_])\s*\1\s*\1[\s\1]*$", ". ", text, flags=re.MULTILINE)

    # 5. Ordered lists — "First, item. Next, item. Next, ..."
    #    Split into lines, detect numbered items, replace inline.
    lines = text.split("\n")
    result_lines: list[str] = []
    ordered_index = 0  # tracks position within the current ordered list
    for line in lines:
        m = re.match(r"^\s*\d+\.\s+(.*)", line)
        if m:
            item = m.group(1).strip()
            prefix = "First, " if ordered_index == 0 else "Next, "
            result_lines.append(f"{prefix}{item}.")
            ordered_index += 1
        else:
            ordered_index = 0  # reset when we leave the list
            result_lines.append(line)
    text = "\n".join(result_lines)

    # 6. Unordered lists — "item. "
    text = re.sub(r"^\s*[-*+]\s+(.*)", r"\1.", text, flags=re.MULTILINE)

    # 7. Bold/italic — keep inner text (order matters: *** before ** before *)
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*(.+?)\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_{3}(.+?)_{3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_{2}(.+?)_{2}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_(.+?)_", r"\1", text, flags=re.DOTALL)

    # 8. Links — keep label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # 9. Newlines → ". "
    text = re.sub(r"\n+", ". ", text)

    # 10. Emoji
    text = _EMOJI_RE.sub("", text)

    # 11. Normalise whitespace
    text = re.sub(r" {2,}", " ", text)

    return text.strip()


class TTSTextSanitizerProcessor(FrameProcessor):
    """Sanitizes TTSSpeakFrame text to remove markdown and emoji before TTS synthesis.

    Insert between AgentCoreLLMProcessor and RayaTTSService in the Pipecat
    pipeline. Intercepts every TTSSpeakFrame, applies _sanitize(), and
    re-emits a new TTSSpeakFrame with the cleaned text. All other frame types
    are passed through unchanged with their original direction.

    Takes no constructor arguments — sanitization rules are fixed and not
    domain-configurable.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Sanitize TTSSpeakFrame text; pass all other frames through.

        Args:
            frame: Incoming pipeline frame.
            direction: Direction of frame flow in the pipeline.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSSpeakFrame):
            clean = _sanitize(frame.text)
            await self.push_frame(TTSSpeakFrame(text=clean))
        else:
            await self.push_frame(frame, direction)
```

- [ ] **Step 4: Run tests**

```bash
cd telephony_adapter
uv run pytest tests/pipecat_services/test_tts_sanitizer.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full suite to check for regressions**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add telephony_adapter/src/pipecat_services/tts_sanitizer.py \
        telephony_adapter/tests/pipecat_services/test_tts_sanitizer.py
git commit -m "feat(telephony-adapter): add TTSTextSanitizerProcessor — semantic markdown/emoji sanitization before TTS (closes #69)"
```

---

## Task 3: Wire `TTSTextSanitizerProcessor` into the pipeline

**Files:**
- Modify: `telephony_adapter/src/vobiz_adapter.py`

- [ ] **Step 1: Add the import**

In `telephony_adapter/src/vobiz_adapter.py`, find the imports block (currently lines 26–31):

```python
from src.base import TelephonyAdapterBase, TelephonyError
from src.operators.vobiz_operator import VobizOperator
from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.raya_tts import RayaTTSService
from src.vad.silero_vad import SileroVADWrapper
```

Add one import line so it becomes:

```python
from src.base import TelephonyAdapterBase, TelephonyError
from src.operators.vobiz_operator import VobizOperator
from src.pipecat_services.agent_core_llm import AgentCoreLLMProcessor
from src.pipecat_services.raya_stt import RayaSTTService
from src.pipecat_services.raya_tts import RayaTTSService
from src.pipecat_services.tts_sanitizer import TTSTextSanitizerProcessor
from src.vad.silero_vad import SileroVADWrapper
```

- [ ] **Step 2: Insert `TTSTextSanitizerProcessor()` into the pipeline**

Find the `Pipeline([...])` call in `handle_call()` (currently lines 96–105):

```python
        pipeline = Pipeline(
            [
                transport.input(),
                VADProcessor(vad_analyzer=vad_analyzer),
                stt,
                agent,
                tts,
                transport.output(),
            ]
        )
```

Change to:

```python
        sanitizer = TTSTextSanitizerProcessor()

        pipeline = Pipeline(
            [
                transport.input(),
                VADProcessor(vad_analyzer=vad_analyzer),
                stt,
                agent,
                sanitizer,
                tts,
                transport.output(),
            ]
        )
```

- [ ] **Step 3: Run the full test suite**

```bash
cd telephony_adapter
uv run pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add telephony_adapter/src/vobiz_adapter.py
git commit -m "feat(telephony-adapter): wire TTSTextSanitizerProcessor into Vobiz pipeline"
```

---
