"""
reach_layer/voice/src/pipecat_services/tts_sanitizer.py

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
    "\U0001F700-\U0001F77F"  # alchemical
    "\U0001F780-\U0001F7FF"  # geometric shapes extended
    "\U0001F800-\U0001F8FF"  # supplemental arrows
    "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
    "\U0001FA00-\U0001FA6F"  # chess, etc.
    "\U0001FA70-\U0001FAFF"  # symbols & pictographs extended-A
    "\U0001F1E0-\U0001F1FF"  # regional indicator (flags)
    "\U00002702-\U000027B0"  # dingbats
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

    # 1. Code fences — omit entirely
    text = re.sub(r"```[^\n]*\n[\s\S]*?```", "", text)
    text = re.sub(r"```[^\n]*", "", text)

    # 2. Inline code — keep inner text
    text = re.sub(r"`([^`]*)`", r"\1", text)

    # Process line by line for structural elements that need special joining
    lines = text.split("\n")
    processed: list[str] = []
    ordered_index = 0

    for line in lines:
        # 3. Horizontal rules — convert to empty (will become pause via newline logic)
        if re.match(r"^\s*([-*_])\s*\1\s*\1[\s\1]*$", line):
            processed.append("")
            ordered_index = 0
            continue

        # 4. Headers — keep text + period + trailing space (trailing space is part of spec)
        m = re.match(r"^#{1,6}\s+(.+)$", line)
        if m:
            processed.append(m.group(1).strip() + ". ")
            ordered_index = 0
            continue

        # 5. Ordered lists
        m = re.match(r"^\s*\d+\.\s+(.*)", line)
        if m:
            item = m.group(1).strip()
            prefix = "First, " if ordered_index == 0 else "Next, "
            processed.append(f"{prefix}{item}.")
            ordered_index += 1
            continue

        # 6. Unordered lists
        m = re.match(r"^\s*[-*+]\s+(.*)", line)
        if m:
            item = m.group(1).strip()
            processed.append(f"{item}.")
            ordered_index = 0
            continue

        ordered_index = 0
        processed.append(line)

    # Track if any structural transformation happened (used to decide end-period)
    has_structure = len(processed) > 1 or any(
        re.match(r"^(First,|Next,|\w.*\.)$", p.strip()) for p in processed
    )

    # Filter out empty segments but track their presence for pause insertion
    segments: list[str] = []
    for p in processed:
        stripped = p.strip()
        if stripped:
            segments.append(p)  # preserve trailing space on headers

    # Join segments: if prev ends with period, add space; otherwise add ". "
    text = ""
    for i, part in enumerate(segments):
        if i == 0:
            text = part
        else:
            prev = text.rstrip(" ")
            if prev and prev[-1] in ".!?":
                text = prev + " " + part.lstrip()
            else:
                text = prev + ". " + part.lstrip()

    # 7. Bold/italic — keep inner text (order: *** before ** before *)
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*(.+?)\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_{3}(.+?)_{3}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_{2}(.+?)_{2}", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_(.+?)_", r"\1", text, flags=re.DOTALL)

    # 8. Links — keep label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # 9. Emoji
    text = _EMOJI_RE.sub("", text)

    # 10. Normalise whitespace (but preserve single trailing space for headers)
    text = re.sub(r" {2,}", " ", text)

    # 11. Add trailing period only when multi-line structure was present and last
    #     segment doesn't already end with punctuation
    is_header_only = len(segments) == 1 and segments[0].endswith(". ")
    if has_structure and text and not is_header_only:
        rstripped = text.rstrip()
        if rstripped and rstripped[-1] not in ".!?":
            text = rstripped + "."
        else:
            text = rstripped

    # 12. Strip leading/trailing whitespace.
    #     Exception: single-header output preserves trailing space ("Getting Started. ").
    if is_header_only:
        text = text.lstrip()
    else:
        text = text.strip()

    return text


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
            await self.push_frame(TTSSpeakFrame(text=clean), direction)
        else:
            await self.push_frame(frame, direction)
