"""Builds the Pipecat pipeline for one realtime-voice-test call.

Pipeline shape:
  transport.input → VADProcessor → UserTurnProcessor →
  OpenAIRealtimeLLMService → LatencyObserverProcessor → transport.output

The LLM is configured with our default prompt + the supplied language
hint for transcription. The observer writes per-turn JSONL to the
caller-supplied path.
"""
from __future__ import annotations

from pathlib import Path

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.openai.realtime.events import (
    AudioConfiguration,
    AudioInput,
    AudioOutput,
    PCMAudioFormat,
    SessionProperties,
    TurnDetection,
)
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from latency_observer import LatencyObserverProcessor
from prompts import DEFAULT_PROMPT


# Silero VAD requires 16 kHz pcm16 input; all pipeline audio is resampled to this rate.
PIPELINE_SAMPLE_RATE = 16000


def build_pipeline_task(
    *,
    transport,
    call_sid: str,
    out_path: Path,
    api_key: str,
    model: str,
    voice: str,
    language: str,
    vad_silence_ms: int,
) -> PipelineTask:
    """Assemble the Pipecat pipeline + PipelineTask for one call.

    Args:
        transport: A configured FastAPIWebsocketTransport (built by server.py
            with the VobizFrameSerializer attached).
        call_sid: Vobiz call identifier.
        out_path: Per-call JSONL output path (e.g. results/<ts>_<sid>/turns.jsonl).
        api_key: OpenAI API key.
        model: OpenAI model id (e.g. "gpt-realtime-mini").
        voice: OpenAI voice name (e.g. "alloy").
        language: Language hint for input audio transcription (e.g. "hi").
        vad_silence_ms: Silence threshold in milliseconds for the
            user-turn-stop strategy.

    Returns:
        A PipelineTask ready to be passed to PipelineRunner.run().
    """
    vad = SileroVADAnalyzer()
    user_turn = UserTurnProcessor(
        user_turn_strategies=UserTurnStrategies(
            start=[VADUserTurnStartStrategy()],
            stop=[SpeechTimeoutUserTurnStopStrategy(
                user_speech_timeout=vad_silence_ms / 1000.0,
            )],
        ),
    )

    llm = OpenAIRealtimeLLMService(
        api_key=api_key,
        settings=OpenAIRealtimeLLMService.Settings(
            model=model,
            session_properties=SessionProperties(
                instructions=DEFAULT_PROMPT,
                # Audio-only modality — skip the side-channel text transcript
                # of the bot's reply (we don't capture it, and dropping it
                # avoids the per-turn output text-token cost).
                output_modalities=["audio"],
                audio=AudioConfiguration(
                    input=AudioInput(
                        format=PCMAudioFormat(),
                        # Align OpenAI's server VAD with our local Silero
                        # threshold so the two endpoint detectors agree.
                        # OpenAI's default is ~500 ms which is too short
                        # for Hindi multi-clause utterances and produces
                        # mid-utterance bot replies.
                        turn_detection=TurnDetection(
                            type="server_vad",
                            silence_duration_ms=vad_silence_ms,
                        ),
                    ),
                    output=AudioOutput(format=PCMAudioFormat()),
                ),
                voice=voice,
                tool_choice="auto",
            ),
        ),
    )

    observer = LatencyObserverProcessor(
        call_sid=call_sid,
        out_path=out_path,
        model=model,
        voice=voice,
        language=language,
    )

    pipeline = Pipeline([
        transport.input(),
        VADProcessor(vad_analyzer=vad),
        user_turn,
        llm,
        observer,
        transport.output(),
    ])

    return PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=PIPELINE_SAMPLE_RATE,
            audio_out_sample_rate=PIPELINE_SAMPLE_RATE,
        ),
    )
