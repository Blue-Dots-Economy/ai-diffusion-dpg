# Channel-Aware Prompting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `build_system_prompt()` append a per-channel suffix read from `agent.channels` in domain YAML, so voice turns produce concise spoken-language prompts without affecting web or CLI.

**Architecture:** `agent.channels[channel].system_prompt_suffix` is resolved in the orchestrator before each `build_system_prompt()` call (both `process_turn` and `stream_turn`). The suffix is appended as the final prompt section, after guardrail constraints. Unsupported channels raise `ValueError` immediately in the orchestrator — the LLM is never called. `build_system_prompt()` accepts `channel_config=None` for backward compatibility with direct callers in tests.

**Tech Stack:** Python, pytest, uv, YAML (PyYAML via existing config loader), no new dependencies.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `dev-kit/dev_kit/schemas/agent_core.yaml` | Modify | Add `agent.channels` block with defaults for voice/web/cli |
| `dev-kit/configs/kkb/agent_core.yaml` | Modify | Add KKB-specific `agent.channels` with Hindi voice suffix |
| `agent_core/tests/test_manager_agent.py` | Modify | Add 4 new tests for `channel_config` in `build_system_prompt()` |
| `agent_core/src/manager_agent.py` | Modify | Add `channel_config: dict \| None = None` param; append suffix as final section |
| `agent_core/tests/test_orchestrator.py` | Modify | Add `agent.channels.cli` to `VALID_CONFIG`; add 2 new channel tests |
| `agent_core/src/orchestrator.py` | Modify | Resolve + validate `channel_config` before both `process_turn` and `stream_turn` calls to `build_system_prompt()` |
| `dev-kit/dev_kit/agent/prompts/phases.py` | Modify | Extend reach phase to configure `agent_core.agent.channels` after channel selection |
| `dev-kit/tests/` | Modify | Update reach phase prompt test to assert `agent.channels` instructions appear |

---

## Task 1: Add `agent.channels` to dev-kit schema and KKB config

**Files:**
- Modify: `dev-kit/dev_kit/schemas/agent_core.yaml`
- Modify: `dev-kit/configs/kkb/agent_core.yaml`

- [ ] **Step 1: Open `dev-kit/dev_kit/schemas/agent_core.yaml` and find the `agent:` block**

The block currently ends after `consent_prompt`. Locate it — it looks like:
```yaml
agent:
  primary_model: ""
  fallback_model: ""
  ask_for_consent: false
  consent_prompt: ""
```

- [ ] **Step 2: Add `channels` under `agent:` in the schema**

Append immediately after `consent_prompt: ""`:
```yaml
  channels:                      # per-channel prompt shaping
    voice:
      system_prompt_suffix: "Respond in 1–2 short spoken sentences. No bullet points or markdown."
    web:
      system_prompt_suffix: ""   # no suffix — full formatting preserved
    cli:
      system_prompt_suffix: ""   # no suffix — full formatting preserved
```

- [ ] **Step 3: Open `dev-kit/configs/kkb/agent_core.yaml` and find the `agent:` block**

Currently:
```yaml
agent:
  primary_model: claude-haiku-4-5-20251001
  fallback_model: claude-haiku-4-5-20251001
  ask_for_consent: true
  consent_prompt: "..."
```

- [ ] **Step 4: Add `channels` under `agent:` in the KKB config**

Append immediately after the `consent_prompt` line:
```yaml
  channels:
    voice:
      system_prompt_suffix: "1-2 chhote vakya bolein. Bullet points ya markdown mat use karein."
    web:
      system_prompt_suffix: ""
    cli:
      system_prompt_suffix: ""
```

- [ ] **Step 5: Commit**

```bash
git add dev-kit/dev_kit/schemas/agent_core.yaml dev-kit/configs/kkb/agent_core.yaml
git commit -m "feat(dev-kit): add agent.channels config block with per-channel prompt suffix"
```

---

## Task 2: Write failing tests for `build_system_prompt()` with `channel_config`

**Files:**
- Modify: `agent_core/tests/test_manager_agent.py`

- [ ] **Step 1: Open `agent_core/tests/test_manager_agent.py` and find the `build_system_prompt` section**

It begins around the comment `# build_system_prompt — E1`. Add these four tests at the end of that section, before the guardrail tests.

- [ ] **Step 2: Add the four new tests**

```python
def test_build_system_prompt_voice_suffix_appended():
    """Voice channel_config suffix appears as the last section of the prompt."""
    agent = _make_manager_for_prompt()
    channel_config = {"system_prompt_suffix": "Respond in 1-2 short spoken sentences. No bullet points."}
    result = agent.build_system_prompt(
        "You are a domain agent.",
        "Help with jobs.",
        "hindi",
        "voice",
        {},
        channel_config=channel_config,
    )
    assert result.endswith("Respond in 1-2 short spoken sentences. No bullet points.")


def test_build_system_prompt_empty_suffix_does_not_change_output():
    """Empty system_prompt_suffix leaves the prompt unchanged."""
    agent = _make_manager_for_prompt()
    baseline = agent.build_system_prompt("You are a domain agent.", "", "hindi", "web", {})
    result = agent.build_system_prompt(
        "You are a domain agent.",
        "",
        "hindi",
        "web",
        {},
        channel_config={"system_prompt_suffix": ""},
    )
    assert result == baseline


def test_build_system_prompt_suffix_is_after_guardrails():
    """Suffix must appear after the guardrail constraints section."""
    agent = _make_manager_for_prompt()
    channel_config = {"system_prompt_suffix": "Keep it short."}
    guardrails = {
        "prompt_constraints": ["No financial advice"],
        "required_disclosures": [],
    }
    result = agent.build_system_prompt(
        "You are an agent.",
        "",
        "hindi",
        "voice",
        {},
        channel_config=channel_config,
        guardrail_constraints=guardrails,
    )
    guardrail_pos = result.index("No financial advice")
    suffix_pos = result.index("Keep it short.")
    assert suffix_pos > guardrail_pos


def test_build_system_prompt_none_channel_config_no_suffix():
    """channel_config=None (default) produces the same output as not passing it."""
    agent = _make_manager_for_prompt()
    without = agent.build_system_prompt("You are an agent.", "", "hindi", "cli", {})
    with_none = agent.build_system_prompt(
        "You are an agent.", "", "hindi", "cli", {}, channel_config=None
    )
    assert without == with_none
```

- [ ] **Step 3: Run the new tests and confirm they fail**

```bash
cd agent_core && uv run pytest tests/test_manager_agent.py::test_build_system_prompt_voice_suffix_appended tests/test_manager_agent.py::test_build_system_prompt_empty_suffix_does_not_change_output tests/test_manager_agent.py::test_build_system_prompt_suffix_is_after_guardrails tests/test_manager_agent.py::test_build_system_prompt_none_channel_config_no_suffix -v
```

Expected: 4 FAILs — `TypeError: build_system_prompt() got an unexpected keyword argument 'channel_config'`

---

## Task 3: Implement `channel_config` in `build_system_prompt()`

**Files:**
- Modify: `agent_core/src/manager_agent.py`

- [ ] **Step 1: Open `agent_core/src/manager_agent.py` and update the `build_system_prompt` signature**

Find this signature (line ~203):
```python
    def build_system_prompt(
        self,
        agent_system_prompt: str,
        subagent_system_prompt: str,
        detected_language: str,
        channel: str,
        profile: dict,
        is_resumption: bool = False,
        guardrail_constraints: dict | None = None,
    ) -> str:
```

Replace with:
```python
    def build_system_prompt(
        self,
        agent_system_prompt: str,
        subagent_system_prompt: str,
        detected_language: str,
        channel: str,
        profile: dict,
        channel_config: dict | None = None,
        is_resumption: bool = False,
        guardrail_constraints: dict | None = None,
    ) -> str:
```

- [ ] **Step 2: Update the docstring to document the new parameter**

Find the `Args:` block in the docstring and add after the `guardrail_constraints` line:
```
            channel_config:         Per-channel config dict from agent.channels[channel].
                                    When present, system_prompt_suffix (if non-empty) is
                                    appended as the final prompt section. Defaults to None
                                    (no suffix injected) for backward compatibility.
```

- [ ] **Step 3: Add the suffix injection at the end of the method body**

Find the end of `build_system_prompt()` — the last block before `return "\n\n".join(parts)` is the guardrail constraints block. After it, add:

```python
        suffix = (channel_config or {}).get("system_prompt_suffix", "")
        if suffix:
            parts.append(suffix.strip())

        return "\n\n".join(parts)
```

Make sure you remove the existing bare `return "\n\n".join(parts)` line that is currently last.

- [ ] **Step 4: Run the four new tests and confirm they pass**

```bash
cd agent_core && uv run pytest tests/test_manager_agent.py::test_build_system_prompt_voice_suffix_appended tests/test_manager_agent.py::test_build_system_prompt_empty_suffix_does_not_change_output tests/test_manager_agent.py::test_build_system_prompt_suffix_is_after_guardrails tests/test_manager_agent.py::test_build_system_prompt_none_channel_config_no_suffix -v
```

Expected: 4 PASSes

- [ ] **Step 5: Run all manager_agent tests to confirm no regressions**

```bash
cd agent_core && uv run pytest tests/test_manager_agent.py -v
```

Expected: all existing tests still pass (they call `build_system_prompt()` without `channel_config`, which now defaults to `None` — no suffix, no change in output).

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/manager_agent.py agent_core/tests/test_manager_agent.py
git commit -m "feat(agent-core): add channel_config param to build_system_prompt for per-channel suffix"
```

---

## Task 4: Write failing tests for orchestrator channel validation

**Files:**
- Modify: `agent_core/tests/test_orchestrator.py`

- [ ] **Step 1: Add `agent.channels` to `VALID_CONFIG` in `test_orchestrator.py`**

Find `VALID_CONFIG` near the top of the file. It currently looks like:
```python
VALID_CONFIG = {
    "conversation": {...},
    "preprocessing": {...},
    "hitl": {...},
}
```

Add an `"agent"` key with `channels`:
```python
VALID_CONFIG = {
    "agent": {
        "channels": {
            "cli": {"system_prompt_suffix": ""},
            "voice": {"system_prompt_suffix": "Respond in 1-2 short sentences."},
            "web": {"system_prompt_suffix": ""},
        },
    },
    "conversation": {...},
    "preprocessing": {...},
    "hitl": {...},
}
```

(Preserve all existing keys exactly — only add the `"agent"` entry.)

- [ ] **Step 2: Add two new tests at the end of the orchestrator test file**

```python
# ---------------------------------------------------------------------------
# Channel-aware prompting
# ---------------------------------------------------------------------------


def test_process_turn_unsupported_channel_raises_value_error():
    """process_turn raises ValueError immediately for a channel not in agent.channels."""
    agent = _make_agent()
    turn = TurnInput(
        session_id=SESSION_ID,
        user_message="Hello",
        channel="whatsapp",   # not in VALID_CONFIG agent.channels
        timestamp_ms=TIMESTAMP,
    )
    with pytest.raises(ValueError, match="Unsupported channel: whatsapp"):
        agent.process_turn(turn)


def test_process_turn_passes_channel_config_to_build_system_prompt():
    """channel_config resolved from agent.channels is forwarded to build_system_prompt."""
    agent = _make_agent()
    agent.process_turn(_turn_input())   # channel="cli"
    call_kwargs = agent._manager_agent.build_system_prompt.call_args.kwargs
    assert call_kwargs["channel_config"] == {"system_prompt_suffix": ""}
```

- [ ] **Step 3: Run the two new tests and confirm they fail**

```bash
cd agent_core && uv run pytest tests/test_orchestrator.py::test_process_turn_unsupported_channel_raises_value_error tests/test_orchestrator.py::test_process_turn_passes_channel_config_to_build_system_prompt -v
```

Expected:
- `test_process_turn_unsupported_channel_raises_value_error` — FAIL (no ValueError raised yet)
- `test_process_turn_passes_channel_config_to_build_system_prompt` — FAIL (`channel_config` kwarg not passed yet)

---

## Task 5: Update orchestrator to resolve and validate `channel_config`

**Files:**
- Modify: `agent_core/src/orchestrator.py`

- [ ] **Step 1: Add a `_resolve_channel_config` helper at the end of the Orchestrator class private methods section**

Open `agent_core/src/orchestrator.py`. Find the `# Private helpers` or equivalent section and add:

```python
    def _resolve_channel_config(self, channel: str) -> dict:
        """Resolve per-channel config from agent.channels, raising for unsupported channels.

        Args:
            channel: Channel name from the inbound TurnInput.

        Returns:
            Channel config dict with at least system_prompt_suffix key.

        Raises:
            ValueError: If the channel is not present in agent.channels config.
        """
        channels = self._config.get("agent", {}).get("channels", {})
        config = channels.get(channel)
        if config is None:
            raise ValueError(f"Unsupported channel: {channel}")
        return config
```

- [ ] **Step 2: Update `process_turn` — resolve channel_config before the `build_system_prompt` call**

In `process_turn`, find the `build_system_prompt` call (around line 695). Just before it, add:

```python
        channel_config = self._resolve_channel_config(turn_input.channel)
```

Then add `channel_config=channel_config` to the `build_system_prompt` call:

```python
        system = self._manager_agent.build_system_prompt(
            agent_system_prompt=self._workflow.agent_system_prompt,
            subagent_system_prompt=next_subagent.system_prompt,
            detected_language=final_language,
            channel=turn_input.channel,
            profile=profile_context,
            channel_config=channel_config,
            is_resumption=is_resumption,
            guardrail_constraints=guardrail_constraints,
        )
```

- [ ] **Step 3: Update `stream_turn` — same change in the streaming path**

In `stream_turn`, find the second `build_system_prompt` call (around line 2172). Apply the same two changes — add the `_resolve_channel_config` call just before it, and add `channel_config=channel_config` to the call kwargs.

- [ ] **Step 4: Run the two new orchestrator tests and confirm they pass**

```bash
cd agent_core && uv run pytest tests/test_orchestrator.py::test_process_turn_unsupported_channel_raises_value_error tests/test_orchestrator.py::test_process_turn_passes_channel_config_to_build_system_prompt -v
```

Expected: 2 PASSes

- [ ] **Step 5: Run all orchestrator tests to confirm no regressions**

```bash
cd agent_core && uv run pytest tests/test_orchestrator.py -v
```

All existing tests use `channel="cli"` and `VALID_CONFIG` now includes `agent.channels.cli`, so they must all pass.

- [ ] **Step 6: Run the full agent_core test suite with coverage**

```bash
cd agent_core && uv run pytest --cov=src --cov-report=term-missing
```

Expected: ≥70% line coverage on `src/`. No failures.

- [ ] **Step 7: Commit**

```bash
git add agent_core/src/orchestrator.py agent_core/tests/test_orchestrator.py
git commit -m "feat(agent-core): resolve and validate channel_config in orchestrator for channel-aware prompting"
```

---

## Task 6: Update dev-kit reach phase prompt

**Files:**
- Modify: `dev-kit/dev_kit/agent/prompts/phases.py`
- Modify (if exists): `dev-kit/agent/tests/test_prompts_phases.py` or equivalent

- [ ] **Step 1: Open `dev-kit/dev_kit/agent/prompts/phases.py` and find the reach phase block**

It begins with `if phase == "reach":` around line 283.

- [ ] **Step 2: Add `agent.channels` configuration instructions to the reach phase prompt**

Find the end of the **Step 2** channel configuration instructions (just before `"The \`update_config\` tool will return an ERROR..."`). Add a new **Step 3** section:

```python
            "**Step 3 — Configure channel response style (agent_core):**\n"
            "For each selected channel, show the user the default system_prompt_suffix from the template below\n"
            "and ask if they want to customise it. Then call:\n"
            "  update_config(block=agent_core, section=agent.channels, values={\n"
            "    'voice': {'system_prompt_suffix': '...'}, \n"
            "    'web':   {'system_prompt_suffix': ''}, \n"
            "    'cli':   {'system_prompt_suffix': ''}\n"
            "  })\n"
            "Only include keys for channels selected in Step 1.\n"
            "The voice default ('Respond in 1–2 short spoken sentences. No bullet points or markdown.')\n"
            "is a good starting point — the user can keep it or write their own in their domain language.\n\n"
```

Insert this block after the CLI channel block and before the existing `"**Domain (all channels):**"` line.

The full reach phase block after the change should read (condensed):

```
Step 1 — Channel selection
Step 2 — Configure ONLY selected channels  (web / CLI / voice reach_layer settings)
Step 3 — Configure channel response style (agent_core agent.channels)
Domain (all channels): ...
```

- [ ] **Step 3: Run dev-kit tests**

```bash
cd dev-kit && uv run pytest -v
```

Expected: all tests pass. If a test asserts on the exact reach phase prompt text, update it to include the new Step 3 text.

- [ ] **Step 4: Commit**

```bash
git add dev-kit/dev_kit/agent/prompts/phases.py
git commit -m "feat(dev-kit): add agent.channels config step to reach phase prompt"
```

---

## Final check

- [ ] **Run both module test suites together**

```bash
cd agent_core && uv run pytest --cov=src --cov-report=term-missing -q
cd ../dev-kit && uv run pytest -q
```

Expected: no failures, agent_core coverage ≥70%.

- [ ] **Smoke check the YAML configs parse correctly**

```bash
cd dev-kit && python -c "
import yaml
with open('dev_kit/schemas/agent_core.yaml') as f:
    d = yaml.safe_load(f)
assert 'channels' in d['agent'], 'missing agent.channels in schema'
assert 'voice' in d['agent']['channels']
print('schema OK')
with open('configs/kkb/agent_core.yaml') as f:
    d = yaml.safe_load(f)
assert 'channels' in d['agent'], 'missing agent.channels in kkb config'
assert d['agent']['channels']['voice']['system_prompt_suffix']
print('kkb config OK')
"
```

Expected output:
```
schema OK
kkb config OK
```
