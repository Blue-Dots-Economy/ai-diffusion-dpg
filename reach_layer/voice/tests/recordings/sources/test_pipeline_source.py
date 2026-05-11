"""Tests for PipelineRecordingSource."""
from __future__ import annotations

import pytest

from src.recordings.sources.pipeline_source import PipelineRecordingSource


@pytest.mark.asyncio
async def test_pipeline_source_exposes_processor():
    src = PipelineRecordingSource(sample_rate=8000)
    procs = src.pipeline_processors
    assert len(procs) == 1


@pytest.mark.asyncio
async def test_begin_activates_processor():
    src = PipelineRecordingSource(sample_rate=8000)
    proc = src.pipeline_processors[0]
    await src.begin(call_sid="CA1", vobiz_call_id="")
    assert proc._active is True


@pytest.mark.asyncio
async def test_end_returns_payload_with_bytes():
    src = PipelineRecordingSource(sample_rate=8000)
    await src.begin(call_sid="CA1", vobiz_call_id="")
    payload = await src.end()
    assert payload.bytes_data is not None
    assert payload.fetch_url is None
