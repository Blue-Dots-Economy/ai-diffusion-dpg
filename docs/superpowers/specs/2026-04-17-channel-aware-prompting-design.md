# Channel-Aware Prompt Assembly

**Issue:** GH-97  
**Branch:** GH-97-channel-aware-prompting  
**Status:** Approved — ready for implementation

---

## Problem

`ManagerAgent.build_system_prompt()` assembles the same prompt for every channel. For voice, lengthy subagent instructions and full profile dumps inflate token count and push the LLM toward verbose responses — which TTS speaks in full, adding latency and degrading UX. Voice users need concise spoken-language output; web/CLI users can handle richer formatted text.

The `channel` parameter is already passed into `build_system_prompt()` but used only to inject the bare string `"Channel: {channel}"` — no shaping is applied.

---

## Design

### Config shape

Each domain's `agent_core.yaml` gains an `agent.channels` block. Each key is a channel name; the only field per channel is `system_prompt_suffix` — free text the domain operator tunes.

```yaml
agent:
  channels:
    voice:
      system_prompt_suffix: "Respond in 1–2 short spoken sentences. No bullet points or markdown."
    web:
      system_prompt_suffix: ""
    cli:
      system_prompt_suffix: ""
```

- `system_prompt_suffix` is appended as the final section of the assembled prompt, after guardrail constraints, so it overrides verbosity from subagent prompts.
- An empty string means no suffix is injected — current behaviour preserved.
- If the inbound `channel` is not present in `agent.channels`, `build_system_prompt()` raises `ValueError`. Unsupported channels fail explicitly; there is no silent fallback.

### Data flow

```
domain YAML
  └─ agent.channels[channel].system_prompt_suffix
       │
       ▼
Orchestrator.process_turn / stream_turn
  ├─ resolves channel_config = config.agent.channels.get(turn_input.channel)
  ├─ raises ValueError if channel_config is None
  └─ passes channel_config into ManagerAgent.build_system_prompt()
       │
       ▼
build_system_prompt()
  ├─ assembles existing layers (agent prompt, context, resumption, profile, subagent, guardrails)
  └─ appends suffix from channel_config["system_prompt_suffix"] as final section
```

### Files changed

| File | Change |
|---|---|
| `agent_core/src/manager_agent.py` | Add `channel_config: dict \| None` param to `build_system_prompt()`; append suffix as final section |
| `agent_core/src/orchestrator.py` | Resolve `channel_config` from config before both `process_turn` and `stream_turn` calls to `build_system_prompt()`; raise `ValueError` for unsupported channels |
| `dev-kit/dev_kit/schemas/agent_core.yaml` | Add `agent.channels` block with default templates for voice, web, cli |
| `dev-kit/dev_kit/agent/prompts/phases.py` | Extend reach phase prompt to also configure `agent_core.agent.channels` after channel selection |
| `dev-kit/configs/kkb/agent_core.yaml` | Add KKB-specific `agent.channels` with Hindi/Hinglish voice suffix |
| `agent_core/tests/test_manager_agent.py` | Per-channel prompt tests |

### `build_system_prompt()` signature change

```python
def build_system_prompt(
    self,
    agent_system_prompt: str,
    subagent_system_prompt: str,
    detected_language: str,
    channel: str,
    profile: dict,
    channel_config: dict | None = None,   # ← new
    is_resumption: bool = False,
    guardrail_constraints: dict | None = None,
) -> str:
```

The suffix is the last `parts.append()` call:

```python
suffix = (channel_config or {}).get("system_prompt_suffix", "")
if suffix:
    parts.append(suffix.strip())
```

### Orchestrator change (both process_turn and stream_turn)

```python
channel_config = self._config.agent.channels.get(turn_input.channel)
if channel_config is None:
    raise ValueError(f"Unsupported channel: {turn_input.channel}")

system = self._manager_agent.build_system_prompt(
    ...
    channel_config=channel_config,
)
```

### Dev-kit reach phase addition

After `set_reach_channels` is called, the reach phase prompt instructs the dev-kit agent to:

1. For each selected channel, show the default `system_prompt_suffix` from the schema template.
2. Ask the domain expert whether to customise it.
3. Call `update_config(block=agent_core, section=agent.channels, values={...})` to persist.

Channel behaviour (what the LLM says) and channel deployment (how the channel connects) are configured together in a single reach-phase conversation.

---

## Error handling

- `channel` not in `agent.channels` → `ValueError` raised in orchestrator before `build_system_prompt()` is called. The turn fails fast with a structured error; no LLM call is made.
- `channel_config` present but `system_prompt_suffix` missing or empty → no suffix injected, prompt unchanged.

---

## Testing

New tests in `agent_core/tests/test_manager_agent.py`:

| Test | Assertion |
|---|---|
| voice channel with suffix | suffix appears as last section of assembled prompt |
| web channel with empty suffix | no extra section appended; prompt unchanged |
| cli channel with empty suffix | no extra section appended; prompt unchanged |
| suffix placement | suffix appears after guardrail constraints section |
| unsupported channel in orchestrator | `ValueError` raised before `build_system_prompt()` call |
| `channel_config=None` | no suffix injected (backward-compat default) |

---

## Constraints

- No changes to the system prompt structure for non-voice channels.
- Profile injection is unchanged — all fields are still injected. Domain operators control profile verbosity via the `system_prompt_suffix` text.
- The `channel` parameter already exists on `build_system_prompt()` — this change adds behaviour to it without altering the call sites beyond passing `channel_config`.
- `channel_config` defaults to `None` for backward compatibility with any tests that call `build_system_prompt()` directly without it.
