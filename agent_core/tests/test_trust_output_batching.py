"""Tests for batched per-sentence Trust Layer ``check_output`` calls (GH-196).

Covers both the standalone ``_TrustOutputBatcher`` helper and the
``stream_turn`` integration: batched calls reduce the number of Trust
Layer round-trips during streaming, blocked verdicts gate downstream
sentences from reaching TTS, time-based flush fires when the size
threshold is not yet hit, and turn-end always drains the buffer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import (
    DoneEvent,
    NLUResult,
    SentenceEvent,
    TrustCheckResult,
)
from src.orchestrator import _TrustOutputBatcher

# Reuse the harness from the existing stream test module — keeps the
# AgentCore wiring identical and avoids drift if those helpers change.
from tests.test_stream_turn import _collect_events, _make_agent_core, _make_turn_input


# ---------------------------------------------------------------------------
# Unit tests — _TrustOutputBatcher
# ---------------------------------------------------------------------------


class TestTrustOutputBatcherUnit:
    @pytest.mark.asyncio
    async def test_size_trigger_releases_at_n(self):
        check = AsyncMock(return_value=TrustCheckResult(passed=True, action="allow"))
        b = _TrustOutputBatcher(
            check_output=check,
            session_id="s",
            max_sentences=3,
            max_interval_ms=10_000,
            fallback_message="fallback",
        )
        assert await b.add("a.") == []
        assert await b.add("b.") == []
        released = await b.add("c.")
        assert released == ["a.", "b.", "c."]
        assert check.await_count == 1
        # Single concatenated payload was sent.
        called_with = check.await_args.args[1]
        assert "a." in called_with and "c." in called_with

    @pytest.mark.asyncio
    async def test_nine_sentences_with_n3_yields_three_calls(self):
        check = AsyncMock(return_value=TrustCheckResult(passed=True, action="allow"))
        b = _TrustOutputBatcher(
            check_output=check,
            session_id="s",
            max_sentences=3,
            max_interval_ms=10_000,
            fallback_message="fallback",
        )
        released_total: list[str] = []
        for i in range(9):
            released_total.extend(await b.add(f"s{i}."))
        # All 9 already released — no remainder.
        assert (await b.flush()) == []
        assert check.await_count == 3, "9 sentences batched by 3 → 3 trust calls"
        assert len(released_total) == 9

    @pytest.mark.asyncio
    async def test_block_replaces_batch_with_fallback(self):
        check = AsyncMock(return_value=TrustCheckResult(passed=False, action="block"))
        b = _TrustOutputBatcher(
            check_output=check,
            session_id="s",
            max_sentences=2,
            max_interval_ms=10_000,
            fallback_message="SAFE_MSG",
        )
        await b.add("bad1.")
        released = await b.add("bad2.")
        assert released == ["SAFE_MSG"]
        assert b.was_escalated is True

    @pytest.mark.asyncio
    async def test_time_trigger_with_partial_buffer(self):
        check = AsyncMock(return_value=TrustCheckResult(passed=True, action="allow"))
        clock = {"t": 0.0}

        def fake_time():
            return clock["t"]

        b = _TrustOutputBatcher(
            check_output=check,
            session_id="s",
            max_sentences=10,  # size won't trigger
            max_interval_ms=500,
            fallback_message="fallback",
            time_fn=fake_time,
        )
        # Two sentences at t=0; size threshold is 10 so no flush yet.
        assert await b.add("s1.") == []
        assert await b.add("s2.") == []
        assert check.await_count == 0
        # Advance clock past the 500 ms threshold and tick.
        clock["t"] = 0.6  # 600 ms
        released = await b.maybe_flush_on_tick()
        assert released == ["s1.", "s2."]
        assert check.await_count == 1

    @pytest.mark.asyncio
    async def test_flush_drains_remaining_buffer(self):
        check = AsyncMock(return_value=TrustCheckResult(passed=True, action="allow"))
        b = _TrustOutputBatcher(
            check_output=check,
            session_id="s",
            max_sentences=10,
            max_interval_ms=10_000,
            fallback_message="fallback",
        )
        await b.add("only.")
        released = await b.flush()
        assert released == ["only."]
        assert check.await_count == 1
        # Idempotent — flushing an empty buffer does nothing.
        assert (await b.flush()) == []
        assert check.await_count == 1

    @pytest.mark.asyncio
    async def test_disabled_flushes_per_sentence(self):
        check = AsyncMock(return_value=TrustCheckResult(passed=True, action="allow"))
        b = _TrustOutputBatcher(
            check_output=check,
            session_id="s",
            max_sentences=5,
            max_interval_ms=10_000,
            fallback_message="fallback",
            enabled=False,
        )
        assert await b.add("a.") == ["a."]
        assert await b.add("b.") == ["b."]
        assert check.await_count == 2

    @pytest.mark.asyncio
    async def test_infra_failure_treats_as_allow(self):
        check = AsyncMock(side_effect=RuntimeError("network down"))
        b = _TrustOutputBatcher(
            check_output=check,
            session_id="s",
            max_sentences=2,
            max_interval_ms=10_000,
            fallback_message="fallback",
        )
        await b.add("x.")
        released = await b.add("y.")
        # Spec: trust infra failure → allow, do not crash.
        assert released == ["x.", "y."]
        assert b.was_escalated is False

    @pytest.mark.asyncio
    async def test_empty_and_whitespace_are_ignored(self):
        check = AsyncMock(return_value=TrustCheckResult(passed=True, action="allow"))
        b = _TrustOutputBatcher(
            check_output=check,
            session_id="s",
            max_sentences=2,
            max_interval_ms=10_000,
            fallback_message="fallback",
        )
        assert await b.add("") == []
        assert await b.add("   ") == []
        # Buffer still empty — flush is a no-op.
        assert (await b.flush()) == []
        assert check.await_count == 0

    def test_invalid_thresholds_raise(self):
        check = AsyncMock(return_value=TrustCheckResult(passed=True, action="allow"))
        with pytest.raises(ValueError):
            _TrustOutputBatcher(
                check_output=check,
                session_id="s",
                max_sentences=0,
                max_interval_ms=10,
                fallback_message="x",
            )
        with pytest.raises(ValueError):
            _TrustOutputBatcher(
                check_output=check,
                session_id="s",
                max_sentences=1,
                max_interval_ms=0,
                fallback_message="x",
            )


# ---------------------------------------------------------------------------
# Integration tests — stream_turn() end-to-end behaviour
# ---------------------------------------------------------------------------


def _wire_basic_nlu(agent):
    """Apply the minimum NLU/normaliser mocks shared by streaming tests."""
    agent._language_normaliser = MagicMock()
    agent._language_normaliser.normalise.return_value = ("msg", "english")
    agent._nlu_processor = MagicMock()
    agent._nlu_processor.process.return_value = NLUResult(
        intent="greeting", entities={}, sentiment="neutral", confidence=0.9
    )


def _enable_batching(agent, *, max_sentences=3, max_interval_ms=10_000, enabled=True):
    agent._config.setdefault("trust_client", {})["check_output_batch"] = {
        "enabled": enabled,
        "max_sentences": max_sentences,
        "max_interval_ms": max_interval_ms,
    }


class TestStreamTurnBatching:

    @pytest.mark.asyncio
    async def test_nine_sentence_turn_yields_three_trust_calls(self):
        """9 sentences with N=3 → exactly 3 calls to check_output."""
        agent = _make_agent_core()
        _enable_batching(agent, max_sentences=3)
        _wire_basic_nlu(agent)

        async def mock_stream(*args, **kwargs):
            for i in range(9):
                yield f"Sentence number {i}. "

        agent._llm.stream_call = mock_stream
        agent._async_trust.check_output = AsyncMock(
            return_value=TrustCheckResult(passed=True, action="allow")
        )

        events = await _collect_events(agent, _make_turn_input())
        sentence_events = [e for e in events if isinstance(e, SentenceEvent)]
        assert len(sentence_events) == 9, "all 9 sentences must reach the user"
        assert agent._async_trust.check_output.await_count == 3, (
            "expected 3 batched trust calls, got "
            f"{agent._async_trust.check_output.await_count}"
        )

    @pytest.mark.asyncio
    async def test_block_on_second_batch_drops_remaining_sentences(self):
        """A blocked second batch must prevent later sentences from reaching TTS."""
        agent = _make_agent_core()
        _enable_batching(agent, max_sentences=3)
        _wire_basic_nlu(agent)

        async def mock_stream(*args, **kwargs):
            for i in range(9):
                yield f"Sentence number {i}. "

        agent._llm.stream_call = mock_stream

        call_idx = {"n": 0}

        async def fake_check(_session_id, _text):
            call_idx["n"] += 1
            if call_idx["n"] == 2:
                return TrustCheckResult(passed=False, action="block", reason="x")
            return TrustCheckResult(passed=True, action="allow")

        agent._async_trust.check_output = AsyncMock(side_effect=fake_check)

        events = await _collect_events(agent, _make_turn_input())
        sentence_events = [e for e in events if isinstance(e, SentenceEvent)]
        done_events = [e for e in events if isinstance(e, DoneEvent)]

        # Batch 1 (3 sentences) emitted, batch 2 replaced by a single fallback,
        # batches 3+ never emitted. Total: 3 + 1 = 4 SentenceEvents.
        assert len(sentence_events) == 4, (
            f"expected 4 events (3 ok + 1 fallback), got "
            f"{[e.text for e in sentence_events]}"
        )
        # Sentences 3..8 must NOT be present.
        joined_text = " ".join(e.text for e in sentence_events)
        for forbidden in ("Sentence number 3", "Sentence number 8"):
            assert forbidden not in joined_text
        assert done_events[0].was_escalated is True
        # Trust was called twice (once for batch 1 ok, once for batch 2 block).
        # Once blocked, the orchestrator stops feeding sentences — no third call.
        assert agent._async_trust.check_output.await_count == 2

    @pytest.mark.asyncio
    async def test_time_based_flush_with_partial_buffer(self):
        """If only 2 sentences accumulated but the M-ms timer trips, flush once."""
        agent = _make_agent_core()
        _enable_batching(agent, max_sentences=10, max_interval_ms=500)
        _wire_basic_nlu(agent)

        # Drive monotonic time forward between tokens.
        clock = {"t": 0.0}
        import src.orchestrator as orch_mod

        original_monotonic = orch_mod.time.monotonic

        def fake_monotonic():
            return clock["t"]

        orch_mod.time.monotonic = fake_monotonic
        try:
            async def mock_stream(*args, **kwargs):
                clock["t"] = 0.0
                yield "Sentence one. "
                # Advance past the 500 ms threshold BEFORE the second
                # sentence arrives — the timed flush should fire on
                # add(s2), releasing [s1, s2] in a single trust call.
                clock["t"] = 0.6
                yield "Sentence two. "
                # Third sentence starts a fresh batch which will be
                # drained by the turn-end flush — second trust call.
                clock["t"] = 0.65
                yield "Sentence three. "

            agent._llm.stream_call = mock_stream
            agent._async_trust.check_output = AsyncMock(
                return_value=TrustCheckResult(passed=True, action="allow")
            )

            events = await _collect_events(agent, _make_turn_input())
        finally:
            orch_mod.time.monotonic = original_monotonic

        sentence_events = [e for e in events if isinstance(e, SentenceEvent)]
        assert len(sentence_events) == 3
        # Two trust calls expected: one timed flush of (s1, s2) and one
        # turn-end flush of (s3). NOT three (would prove no batching) and
        # NOT one (would prove the time trigger never fired).
        assert agent._async_trust.check_output.await_count == 2

    @pytest.mark.asyncio
    async def test_turn_end_flush_drains_partial_buffer(self):
        """A short turn (< N sentences) still triggers a single flush at end."""
        agent = _make_agent_core()
        _enable_batching(agent, max_sentences=5, max_interval_ms=10_000)
        _wire_basic_nlu(agent)

        async def mock_stream(*args, **kwargs):
            yield "Only one. "
            yield "And two. "

        agent._llm.stream_call = mock_stream
        agent._async_trust.check_output = AsyncMock(
            return_value=TrustCheckResult(passed=True, action="allow")
        )

        events = await _collect_events(agent, _make_turn_input())
        sentence_events = [e for e in events if isinstance(e, SentenceEvent)]
        assert [e.text for e in sentence_events] == ["Only one.", "And two."]
        # Single batched call drains both at turn end.
        assert agent._async_trust.check_output.await_count == 1
