# Voice Audio Test — gpt-audio-mini end-to-end

Test OpenAI's `gpt-audio-mini` (or any audio chat model) with full
audio-in / audio-out, no other infrastructure. Useful for experiencing
the model's actual voice behaviour in any language including Hindi.

## What it does

1. Takes audio input (either a file you provide, or generated from text via `tts-1`).
2. Sends to `gpt-audio-mini` with `modalities=["text", "audio"]`.
3. Saves the audio response to `samples/response.wav`.
4. Optionally auto-plays it (macOS `afplay`, Linux `aplay`).

The model handles the full voice pipeline internally — no separate
ASR/TTS — and produces both a text transcript and the spoken response
in one call.

## Setup

```bash
cd experiments/voice_audio_test
uv sync
export OPENAI_API_KEY=sk-...
```

## Quick start — text-to-voice loop in Hindi

The fastest way to experience the model. No mic needed:

```bash
uv run python voice_test.py \
  --input-text "नमस्ते, मैं Hubballi में electrician का काम ढूंढ रहा हूँ" \
  --play
```

What happens:
1. `tts-1` generates Hindi audio from your text → `samples/input_generated.mp3`
2. That audio file is sent to `gpt-audio-mini`
3. Model thinks, replies in Hindi audio + transcript
4. Response is saved to `samples/response.wav` and played back

You'll hear the assistant's Hindi response. The transcript is also
printed to stderr.

## With a real audio recording

Record yourself on your phone or with QuickTime Player, save as `.m4a`
or `.mp3`, then:

```bash
uv run python voice_test.py \
  --input-audio samples/my_recording.m4a \
  --play
```

Works with `mp3`, `wav`, `flac`, `opus`, `m4a` (sent as `mp4`), `ogg`.

## Voice options

OpenAI has multiple voices. Hindi quality varies by voice — try a few:

```bash
uv run python voice_test.py --input-text "..." --voice alloy --play   # neutral
uv run python voice_test.py --input-text "..." --voice nova --play    # warm female
uv run python voice_test.py --input-text "..." --voice shimmer --play # softer female
uv run python voice_test.py --input-text "..." --voice onyx --play    # deep male
uv run python voice_test.py --input-text "..." --voice sage --play    # newer voice
```

The full list: `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`,
`nova`, `onyx`, `sage`, `shimmer`, `verse`.

## Customising the assistant's behaviour

The default system prompt tells the model "you are a Hindi-speaking
assistant, reply in 1-2 short sentences." Override with `--system`:

```bash
uv run python voice_test.py \
  --input-text "मैं काम की बात से बात कर रहा हूँ" \
  --system "तुम काम की बात हो — Indian workers की voice guide। हमेशा Hindi में जवाब दो। केवल काम और career की बात करो।" \
  --play
```

## Other useful flags

- `--model gpt-audio` — try the bigger model (more expensive, possibly higher quality)
- `--output some_path.wav` — save response somewhere specific
- `--max-tokens 500` — allow longer responses

## What you'll see in stderr

```
Generating input audio with tts-1 voice='alloy'...
  → samples/input_generated.mp3 (15,234 bytes)
Input: samples/input_generated.mp3 (15,234 bytes, format='mp3')
Calling gpt-audio-mini (voice='alloy')...

=== Response ===
  audio:      samples/response.wav (87,654 bytes)
  transcript: नमस्ते। Hubballi में electrician की कई vacancies हैं — मैं आपकी profile देख कर कुछ options भेज सकती हूँ। शुरू करूँ?
  latency:    3,847 ms
  prompt_tokens:     1,234
  completion_tokens: 287
  prompt_tokens_details: ...
  completion_tokens_details: ...
Playing samples/response.wav...
```

## Cost note

`gpt-audio-mini` audio I/O is billed per minute / per audio token, not
per text token. Each call typically costs **a few cents** for short
exchanges. Don't loop this in a tight script unless you're aware of
the rate.

The `tts-1` step (when using `--input-text`) is billed separately at
text-to-speech rates, also cents per call. Cheap, but adds up.

## Limitations

- One-shot only — this script does a single turn (audio in → audio out)
  and exits. No multi-turn conversation state.
- No streaming — the script waits for the full response before saving.
  Real production voice DPGs would stream the response chunks for
  lower TTFT.
- File-based — uses local disk for audio files. Real production would
  stream input/output bytes.

For a true conversational voice loop with streaming, mic capture, and
multi-turn state, look at `reach_layer/voice/` in the main DPG repo —
that's the production voice channel implementation.
