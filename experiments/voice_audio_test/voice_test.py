"""End-to-end voice loop test against OpenAI's gpt-audio-mini (or any audio
chat model). Audio in, audio out — handles full Hindi voice conversation
in a single API call.

Four ways to provide input audio:
  --mic                            record live from your laptop mic
  --prompt N                       use canned Hindi prompt #N from sample_prompts.py
  --input-audio path/to/file.mp3   use an existing audio file
  --input-text "नमस्ते ..."         generate Hindi audio first via tts-1

Output audio is saved to disk and (optionally) played via afplay on
macOS or aplay on Linux.

Examples:
  # Demo with canned prompts (no typing Hindi or talking)
  uv run python voice_test.py --list-prompts            # see all 10
  uv run python voice_test.py --prompt 5 --voice nova --play

  # Live mic — speak Hindi, hear gpt-audio-mini reply
  uv run python voice_test.py --mic --voice nova --play

  # Type Hindi text inline instead of speaking
  uv run python voice_test.py --input-text "नमस्ते, मुझे काम चाहिए" --play

  # Use a recording you made on your phone
  uv run python voice_test.py --input-audio samples/my_recording.m4a --play
"""
from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import time
from pathlib import Path

from openai import OpenAI

from sample_prompts import SAMPLE_PROMPTS, get_prompt, list_prompts


HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "samples"


# Voices accepted by BOTH tts-1 (used to synthesise input audio when
# --input-text or --prompt is passed) AND gpt-audio-mini output. tts-1
# is the stricter set, so we use its 9 voices only. Voices like 'ballad'
# and 'verse' work on newer TTS models (gpt-4o-mini-tts) but not on
# tts-1, which would reject them with a 400.
VOICES = [
    "alloy", "ash", "coral", "echo", "fable",
    "nova", "onyx", "sage", "shimmer",
]

# Audio formats accepted as INPUT by the audio chat model.
INPUT_FORMATS = {"mp3", "wav", "flac", "opus", "m4a", "ogg"}

# Output format the model returns. wav is universally playable.
OUTPUT_FORMAT = "wav"


def record_from_mic(samplerate: int = 16000) -> Path:
    """Record from default mic until user presses Enter, save as WAV.

    Uses sounddevice with a callback running on its own audio thread so
    the main thread can block on input() waiting for Enter.
    """
    import sounddevice as sd  # imported lazily; not needed for file/text modes
    import numpy as np
    import wave

    SAMPLES.mkdir(parents=True, exist_ok=True)
    out = SAMPLES / "input_recorded.wav"
    frames: list[np.ndarray] = []

    def callback(indata, _frames, _time_info, status):
        if status:
            print(f"  ⚠ {status}", file=sys.stderr)
        frames.append(indata.copy())

    print("🎙️  Recording from default mic — speak now.", file=sys.stderr)
    print("    Press Enter when done...", file=sys.stderr)
    with sd.InputStream(samplerate=samplerate, channels=1, dtype="int16",
                        callback=callback):
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    print("⏹️  Recording stopped.", file=sys.stderr)

    if not frames:
        print("ERROR: no audio captured. Is your mic connected?",
              file=sys.stderr)
        sys.exit(1)

    audio = np.concatenate(frames, axis=0)
    duration_s = len(audio) / samplerate
    print(f"  → {out} ({duration_s:.1f}s, {audio.nbytes:,} bytes)",
          file=sys.stderr)

    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(samplerate)
        wf.writeframes(audio.tobytes())

    return out


def encode_audio(path: Path) -> tuple[str, str]:
    """Read an audio file, return (base64_data, format_str)."""
    fmt = path.suffix.lower().lstrip(".")
    if fmt == "m4a":
        # The API expects mp4-family audio as either "mp4" or via mp3
        # transcoding. Most m4a files work when sent as "mp4".
        fmt = "mp4"
    if fmt not in INPUT_FORMATS and fmt != "mp4":
        print(f"WARN: unfamiliar input format '{fmt}'. Trying anyway.",
              file=sys.stderr)
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii"), fmt


def generate_input_from_text(client: OpenAI, text: str, voice: str) -> Path:
    """Use tts-1 to synthesize Hindi (or any language) text → mp3 file."""
    print(f"Generating input audio with tts-1 voice='{voice}'...",
          file=sys.stderr)
    SAMPLES.mkdir(parents=True, exist_ok=True)
    out = SAMPLES / "input_generated.mp3"
    resp = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="mp3",
    )
    out.write_bytes(resp.read())
    print(f"  → {out} ({out.stat().st_size:,} bytes)", file=sys.stderr)
    return out


def play_audio(path: Path) -> None:
    """Best-effort cross-platform playback."""
    if sys.platform == "darwin":
        cmd = ["afplay", str(path)]
    elif sys.platform.startswith("linux"):
        cmd = ["aplay", str(path)]
    else:
        print(f"  (no auto-play on {sys.platform}; open {path} manually)",
              file=sys.stderr)
        return
    print(f"Playing {path}...", file=sys.stderr)
    subprocess.run(cmd, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # --list-prompts is a separate utility flag, not part of input source group
    parser.add_argument("--list-prompts", action="store_true",
                        help="List the canned Hindi demo prompts and exit.")

    src = parser.add_mutually_exclusive_group()
    src.add_argument("--mic", action="store_true",
                     help="Record live from default microphone until Enter "
                          "is pressed. Requires sounddevice + numpy.")
    src.add_argument("--prompt", type=int, metavar="N",
                     help=f"Use canned demo prompt N (1-{len(SAMPLE_PROMPTS)}). "
                          "See --list-prompts.")
    src.add_argument("--input-audio", type=Path,
                     help="Path to an audio file to send to the model.")
    src.add_argument("--input-text",
                     help="Text to convert to audio via tts-1 first, "
                          "then send. Useful for testing without a mic.")
    parser.add_argument("--output", type=Path,
                        default=SAMPLES / "response.wav",
                        help="Where to save the model's audio response.")
    parser.add_argument("--model", default="gpt-audio-mini",
                        help="OpenAI audio chat model. Default: gpt-audio-mini")
    parser.add_argument("--voice", default="alloy", choices=VOICES,
                        help="Output voice (and tts-1 voice if generating "
                             "input). Default: alloy")
    parser.add_argument(
        "--system",
        default=(
            "You are a helpful, friendly Hindi-speaking assistant. The user "
            "is speaking Hindi. Listen carefully, then reply in natural, "
            "conversational Hindi. Keep your answer concise — 1 to 2 short "
            "sentences."
        ),
        help="System prompt to control the assistant's behaviour.",
    )
    parser.add_argument("--play", action="store_true",
                        help="Auto-play the response audio after receiving.")
    parser.add_argument("--max-tokens", type=int, default=300,
                        help="Max tokens in the response. Audio output uses "
                             "tokens too — keep this generous.")
    args = parser.parse_args()

    # Handle the listing utility before requiring an API key — it doesn't
    # call the API.
    if args.list_prompts:
        print(list_prompts())
        sys.exit(0)

    # Now require exactly one input source (parser made the group optional
    # so --list-prompts could run alone; enforce here for the actual call).
    sources = [args.mic, args.prompt is not None,
               args.input_audio is not None, args.input_text is not None]
    if sum(sources) != 1:
        print("ERROR: pass exactly one of --mic, --prompt, --input-audio, "
              "--input-text  (or --list-prompts to see canned prompts).",
              file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = OpenAI()

    # ── Step 1: prepare input audio ──────────────────────────────────
    if args.mic:
        input_path = record_from_mic()
    elif args.prompt is not None:
        try:
            chosen = get_prompt(args.prompt)
        except IndexError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Using prompt #{chosen['id']}: {chosen['label']}",
              file=sys.stderr)
        print(f'  "{chosen["text"]}"', file=sys.stderr)
        input_path = generate_input_from_text(client, chosen["text"],
                                              args.voice)
    elif args.input_text:
        input_path = generate_input_from_text(client, args.input_text,
                                              args.voice)
    else:
        input_path = args.input_audio
        if not input_path.exists():
            print(f"ERROR: {input_path} not found", file=sys.stderr)
            sys.exit(1)

    audio_b64, input_format = encode_audio(input_path)
    print(f"Input: {input_path} ({input_path.stat().st_size:,} bytes, "
          f"format='{input_format}')", file=sys.stderr)

    # ── Step 2: call the audio chat model ────────────────────────────
    print(f"Calling {args.model} (voice='{args.voice}')...",
          file=sys.stderr)
    start = time.perf_counter()
    resp = client.chat.completions.create(
        model=args.model,
        modalities=["text", "audio"],
        audio={"voice": args.voice, "format": OUTPUT_FORMAT},
        max_completion_tokens=args.max_tokens,
        messages=[
            {"role": "system", "content": args.system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio_b64,
                            "format": input_format,
                        },
                    }
                ],
            },
        ],
    )
    latency_ms = int((time.perf_counter() - start) * 1000)

    # ── Step 3: extract response ────────────────────────────────────
    msg = resp.choices[0].message
    usage = resp.usage

    if not getattr(msg, "audio", None):
        print("ERROR: model returned no audio.", file=sys.stderr)
        print(f"  Text content: {getattr(msg, 'content', '<none>')}",
              file=sys.stderr)
        sys.exit(2)

    audio_bytes = base64.b64decode(msg.audio.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(audio_bytes)

    print()
    print(f"=== Response ===", file=sys.stderr)
    print(f"  audio:      {args.output} ({len(audio_bytes):,} bytes)",
          file=sys.stderr)
    if getattr(msg.audio, "transcript", None):
        print(f"  transcript: {msg.audio.transcript}", file=sys.stderr)
    print(f"  latency:    {latency_ms:,} ms", file=sys.stderr)
    if usage:
        print(f"  prompt_tokens:     {usage.prompt_tokens}", file=sys.stderr)
        print(f"  completion_tokens: {usage.completion_tokens}",
              file=sys.stderr)
        # Audio models break tokens into text+audio sub-types. Print whatever's there.
        for attr in ("prompt_tokens_details", "completion_tokens_details"):
            d = getattr(usage, attr, None)
            if d:
                print(f"  {attr}: {d}", file=sys.stderr)

    # ── Step 4: optionally play ─────────────────────────────────────
    if args.play:
        play_audio(args.output)


if __name__ == "__main__":
    main()
