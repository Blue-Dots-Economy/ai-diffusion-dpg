"""Per-call coordinator: Vobiz WS ↔ OpenAI Realtime WS + JSONL capture.

The bridge has two responsibilities:
  1. Proxy audio bytes between Vobiz WS and OpenAI Realtime WS.
  2. Watch OpenAI events to capture per-turn latency markers and write
     them as JSONL rows.

The pure state-machine logic (TurnAccumulator) is tested in isolation.
The network bridge (run() function) is exercised end-to-end by real
phone calls.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Any

from openai_realtime import RealtimeClient
from pricing import compute_turn_cost
from vobiz_protocol import (
    MediaFrame,
    StartFrame,
    StopFrame,
    UnknownFrame,
    build_play_audio,
    parse_frame,
)

logger = logging.getLogger(__name__)


class TurnAccumulator:
    """Per-call state machine that produces one JSONL row per user turn.

    Feed it OpenAI events via observe(), audio-byte counts via
    note_audio_in_bytes / note_audio_out_bytes, and pop completed turns
    via pop_finished_turn().
    """

    def __init__(
        self,
        *,
        call_sid: str,
        session_id: str,
        model: str,
        voice: str,
        prompt_name: str,
    ) -> None:
        """Initialize a TurnAccumulator.

        Args:
            call_sid: The call session identifier.
            session_id: The application session identifier.
            model: The OpenAI model name (e.g., "gpt-realtime-mini").
            voice: The voice name (e.g., "alloy").
            prompt_name: The name of the system prompt used.
        """
        self._call_sid = call_sid
        self._session_id = session_id
        self._model = model
        self._voice = voice
        self._prompt_name = prompt_name
        self._turn_num = 0
        self._cur: dict[str, Any] = {}
        self._finished: list[dict[str, Any]] = []

    def observe(self, now_ms: int, event: dict[str, Any]) -> None:
        """Process one OpenAI event, advancing the state machine.

        Args:
            now_ms: Timestamp in milliseconds (relative to call start).
            event: The parsed OpenAI Realtime event dict.
        """
        etype = event.get("type", "")

        if etype == "input_audio_buffer.speech_started":
            self._turn_num += 1
            self._cur = {
                "call_sid": self._call_sid,
                "session_id": self._session_id,
                "turn": self._turn_num,
                "model": self._model,
                "voice": self._voice,
                "prompt_name": self._prompt_name,
                "t_speech_started_ms": now_ms,
                "audio_in_bytes": 0,
                "audio_out_bytes": 0,
                "turn_started_at_iso": dt.datetime.now(dt.timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
            }
            return

        if not self._cur:
            return  # no turn in progress; ignore stray event

        if etype == "input_audio_buffer.speech_stopped":
            self._cur["t_speech_stopped_ms"] = now_ms
        elif etype == "response.created":
            self._cur["t_response_created_ms"] = now_ms
        elif etype == "response.audio.delta":
            if "t_first_audio_byte_ms" not in self._cur:
                self._cur["t_first_audio_byte_ms"] = now_ms
        elif etype == "conversation.item.input_audio_transcription.completed":
            self._cur["transcript_in"] = event.get("transcript", "")
        elif etype == "response.audio_transcript.done":
            self._cur["transcript_out"] = event.get("transcript", "")
        elif etype == "response.done":
            self._cur["t_response_done_ms"] = now_ms
            self._finalise_turn(event)

    def note_audio_in_bytes(self, n: int) -> None:
        """Add to the current turn's incoming audio byte count.

        Args:
            n: Number of bytes to add.
        """
        if self._cur:
            self._cur["audio_in_bytes"] = self._cur.get("audio_in_bytes", 0) + n

    def note_audio_out_bytes(self, n: int) -> None:
        """Add to the current turn's outgoing audio byte count.

        Args:
            n: Number of bytes to add.
        """
        if self._cur:
            self._cur["audio_out_bytes"] = self._cur.get("audio_out_bytes", 0) + n

    def pop_finished_turn(self) -> dict[str, Any] | None:
        """Return the oldest finished turn, or None if none ready.

        Returns:
            A completed turn dict with all latency and cost fields, or None.
        """
        if not self._finished:
            return None
        return self._finished.pop(0)

    def _finalise_turn(self, response_done_event: dict[str, Any]) -> None:
        """Extract usage from response.done and stash the turn.

        Args:
            response_done_event: The response.done event from OpenAI.
        """
        cur = self._cur

        usage = (response_done_event.get("response") or {}).get("usage") or {}
        in_details = usage.get("input_token_details") or {}
        out_details = usage.get("output_token_details") or {}

        cur["input_text_tokens"] = int(in_details.get("text_tokens", 0) or 0)
        cur["input_audio_tokens"] = int(in_details.get("audio_tokens", 0) or 0)
        cur["input_cached_tokens"] = int(in_details.get("cached_tokens", 0) or 0)
        cur["output_text_tokens"] = int(out_details.get("text_tokens", 0) or 0)
        cur["output_audio_tokens"] = int(out_details.get("audio_tokens", 0) or 0)

        # Derived latency metrics.
        t_stopped = cur.get("t_speech_stopped_ms")
        t_first = cur.get("t_first_audio_byte_ms")
        t_done = cur.get("t_response_done_ms")
        t_created = cur.get("t_response_created_ms")
        t_started = cur.get("t_speech_started_ms")

        if t_stopped is not None and t_first is not None:
            cur["ttft_ms"] = t_first - t_stopped
        if t_stopped is not None and t_done is not None:
            cur["total_response_ms"] = t_done - t_stopped
        if t_stopped is not None and t_created is not None:
            cur["response_decision_ms"] = t_created - t_stopped
        if t_started is not None and t_stopped is not None:
            cur["user_speech_duration_ms"] = t_stopped - t_started

        cur["cost_usd"] = round(compute_turn_cost(cur), 8)

        self._finished.append(cur)
        self._cur = {}


def _now_ms_factory():
    """Return a monotonic-clock function that yields ms since first call."""
    start = time.monotonic_ns()

    def now_ms() -> int:
        return int((time.monotonic_ns() - start) / 1_000_000)

    return now_ms


def _write_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append one JSON row to the file and flush. Failures are logged, not raised."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
    except Exception as exc:
        logger.error(
            "bridge.jsonl_write_failed",
            extra={
                "operation": "bridge._write_jsonl",
                "status": "failure",
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


async def run(
    vobiz_ws,
    *,
    call_sid: str,
    api_key: str,
    model: str,
    voice: str,
    prompt_name: str,
    instructions: str,
    vad_silence_ms: int,
    results_dir: Path,
) -> None:
    """Run the per-call bridge until either side disconnects.

    Args:
        vobiz_ws: FastAPI WebSocket already accepted from Vobiz.
        call_sid: Vobiz call identifier (used for the JSONL filename).
        api_key: OpenAI API key.
        model: OpenAI model id (e.g. 'gpt-realtime-mini').
        voice: OpenAI voice name (e.g. 'alloy').
        prompt_name: Name of the prompt registered in prompts.py.
        instructions: The system prompt text to send to OpenAI.
        vad_silence_ms: server_vad silence_duration_ms config.
        results_dir: Folder where the per-call JSONL is written.
    """
    now_ms = _now_ms_factory()
    out_path = results_dir / f"call_{call_sid}.jsonl"

    openai = RealtimeClient(api_key=api_key, model=model)
    await openai.connect()
    await openai.send_session_update(
        instructions=instructions,
        voice=voice,
        vad_silence_ms=vad_silence_ms,
    )

    accumulator = TurnAccumulator(
        call_sid=call_sid,
        session_id="",  # filled in when session.created arrives
        model=model,
        voice=voice,
        prompt_name=prompt_name,
    )

    # State shared between the two tasks.
    stream_id: str = ""
    stop_event = asyncio.Event()

    async def vobiz_to_openai() -> None:
        """Read Vobiz frames; forward audio bytes to OpenAI."""
        nonlocal stream_id
        try:
            while not stop_event.is_set():
                raw = await vobiz_ws.receive_text()
                frame = parse_frame(raw)
                if isinstance(frame, StartFrame):
                    stream_id = frame.stream_id
                    logger.info(
                        "bridge.vobiz_start",
                        extra={
                            "operation": "bridge.vobiz_to_openai",
                            "status": "success",
                            "call_sid": call_sid,
                            "stream_id": frame.stream_id,
                            "vobiz_call_id": frame.call_id,
                        },
                    )
                elif isinstance(frame, MediaFrame):
                    await openai.send_audio(frame.audio_bytes)
                    accumulator.note_audio_in_bytes(len(frame.audio_bytes))
                elif isinstance(frame, StopFrame):
                    logger.info(
                        "bridge.vobiz_stop",
                        extra={
                            "operation": "bridge.vobiz_to_openai",
                            "status": "success",
                            "call_sid": call_sid,
                        },
                    )
                    stop_event.set()
                    return
                elif isinstance(frame, UnknownFrame):
                    logger.debug(
                        "bridge.vobiz_unknown",
                        extra={
                            "operation": "bridge.vobiz_to_openai",
                            "status": "skipped",
                            "call_sid": call_sid,
                            "event": frame.event,
                        },
                    )
        except Exception as exc:
            logger.warning(
                "bridge.vobiz_loop_error",
                extra={
                    "operation": "bridge.vobiz_to_openai",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            stop_event.set()

    async def openai_to_vobiz() -> None:
        """Read OpenAI events; forward response audio to Vobiz; capture markers."""
        try:
            async for event in openai.events():
                if stop_event.is_set():
                    return
                etype = event.get("type", "")

                # Capture session_id on first session.created
                if etype == "session.created":
                    sess = event.get("session") or {}
                    sid = str(sess.get("id", ""))
                    accumulator._session_id = sid  # injected once
                    logger.info(
                        "bridge.openai_session",
                        extra={
                            "operation": "bridge.openai_to_vobiz",
                            "status": "success",
                            "call_sid": call_sid,
                            "session_id": sid,
                        },
                    )

                # Forward audio bytes back to the phone caller.
                if etype == "response.audio.delta":
                    b64 = event.get("delta", "") or ""
                    try:
                        audio_bytes = base64.b64decode(b64) if b64 else b""
                    except Exception:
                        audio_bytes = b""
                    if audio_bytes and stream_id:
                        await vobiz_ws.send_text(build_play_audio(stream_id, audio_bytes))
                        accumulator.note_audio_out_bytes(len(audio_bytes))

                # Latency-marker capture.
                accumulator.observe(now_ms(), event)

                # Flush any finished turns to disk.
                while True:
                    finished = accumulator.pop_finished_turn()
                    if finished is None:
                        break
                    _write_jsonl(out_path, finished)
                    logger.info(
                        "bridge.turn_finished",
                        extra={
                            "operation": "bridge.openai_to_vobiz",
                            "status": "success",
                            "call_sid": call_sid,
                            "turn": finished.get("turn"),
                            "ttft_ms": finished.get("ttft_ms"),
                        },
                    )
        except Exception as exc:
            logger.warning(
                "bridge.openai_loop_error",
                extra={
                    "operation": "bridge.openai_to_vobiz",
                    "status": "failure",
                    "call_sid": call_sid,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            stop_event.set()

    try:
        await asyncio.gather(vobiz_to_openai(), openai_to_vobiz())
    finally:
        await openai.aclose()
        logger.info(
            "bridge.call_complete",
            extra={
                "operation": "bridge.run",
                "status": "success",
                "call_sid": call_sid,
            },
        )
