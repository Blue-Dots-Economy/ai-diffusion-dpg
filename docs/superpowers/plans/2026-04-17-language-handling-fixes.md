# Language Handling Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three language-handling issues in agent_core: remove hardcoded domain content from the language normaliser, prevent per-turn auto-detection from overriding session language preference, and add support for user-requested language switching.

**Architecture:** All changes are confined to `agent_core/src/preprocessing/language_normalisation.py` and `agent_core/src/orchestrator.py`. The language normaliser becomes fully config-driven and gains a short-input bypass. The orchestrator language preference logic is tightened so auto-detection only fires on the first turn; a new `language_switch_request` intent handler after Step 5 (NLU) deals with explicit user switches. Both the sync (`process_turn`) and async streaming (`stream_turn`) paths in the orchestrator receive identical fixes. The schema YAML gains one new optional config key.

**Tech Stack:** Python 3.11+, pytest, uv, existing `LLMWrapperBase`, `ContextBundle`, `TurnResult`, `NLUResult` dataclasses.

**Issues:** #123 (language stability), #124 (normaliser cleanup), #125 (user-requested switch)

---

## File map

| Action | Path |
|---|---|
| Modify | `agent_core/src/preprocessing/language_normalisation.py` |
| Modify | `agent_core/src/orchestrator.py` |
| Modify | `agent_core/tests/test_language_normalisation.py` |
| Modify | `agent_core/tests/test_orchestrator.py` |
| Modify | `dev-kit/dev_kit/schemas/agent_core.yaml` |

---

## Task 1: Fix language_normalisation.py (Issue #124)

**Files:**
- Modify: `agent_core/src/preprocessing/language_normalisation.py`
- Test: `agent_core/tests/test_language_normalisation.py`

### Background

`_LANG_NORM_SYSTEM` is a module-level string constant with two problems:
1. Contains hardcoded domain content: `"Hindi/Kannada/Hinglish employment chatbot"` and `"bijli ka kaam chahiye"`.
2. Does not reference `default_language` from config, so the LLM has no basis for preferring one language over another.

Additional gaps:
- Short inputs (1–2 words) are sent to the LLM even though single words cannot be reliably language-classified.
- The `bhashini` error message references "PoC" (hardcoded scope).

---

- [ ] **Step 1: Write the failing tests**

Add the following to `agent_core/tests/test_language_normalisation.py`. These tests will fail until the implementation is updated.

```python
# ---------------------------------------------------------------------------
# #124 — config-driven prompt, default_language weighting, short-input bypass
# ---------------------------------------------------------------------------

CONFIG_WITH_DEFAULT = {
    "preprocessing": {
        "language_normalisation": {
            "model": "claude-haiku-4-5-20251001",
            "provider": "llm_native",
            "default_language": "hindi",
            "supported_languages": ["hindi", "kannada", "english", "hinglish"],
        }
    }
}


def test_system_prompt_contains_default_language(normaliser):
    """System prompt sent to LLM must mention the configured default_language."""
    llm = make_llm_returning("hello", "english")
    normaliser.normalise("hello world there", CONFIG_WITH_DEFAULT, llm)
    call_kwargs = llm.call.call_args[1]
    assert "hindi" in call_kwargs["system"]


def test_system_prompt_has_no_hardcoded_domain_content(normaliser):
    """No employment-chatbot or domain-specific example text in the system prompt."""
    llm = make_llm_returning("hello", "english")
    normaliser.normalise("hello world there", CONFIG_WITH_DEFAULT, llm)
    call_kwargs = llm.call.call_args[1]
    assert "employment" not in call_kwargs["system"]
    assert "bijli" not in call_kwargs["system"]


def test_short_input_skips_llm_and_returns_default_language(normaliser):
    """Input with fewer than min_detection_tokens words skips LLM call."""
    llm = MagicMock()
    normalised, detected = normaliser.normalise("ok", CONFIG_WITH_DEFAULT, llm)
    llm.call.assert_not_called()
    assert normalised == "ok"
    assert detected == "hindi"


def test_short_input_default_token_threshold_is_three(normaliser):
    """Exactly 3 words should trigger the LLM; fewer should not."""
    llm = make_llm_returning("hello world foo", "english")
    normaliser.normalise("hello world foo", CONFIG_WITH_DEFAULT, llm)
    llm.call.assert_called_once()  # 3 words → LLM called

    llm2 = MagicMock()
    normaliser.normalise("hello world", CONFIG_WITH_DEFAULT, llm2)
    llm2.call.assert_not_called()  # 2 words → skipped


def test_custom_min_detection_tokens_respected(normaliser):
    """min_detection_tokens from config overrides the default of 3."""
    cfg = {
        "preprocessing": {
            "language_normalisation": {
                "provider": "llm_native",
                "default_language": "english",
                "supported_languages": ["english"],
                "min_detection_tokens": 1,
            }
        }
    }
    llm = make_llm_returning("ok", "english")
    normaliser.normalise("ok", cfg, llm)
    llm.call.assert_called_once()  # threshold = 1 → single word triggers LLM


def test_bhashini_error_message_is_generic(normaliser):
    """Bhashini error message must not reference 'PoC' or domain-specific scope."""
    bhashini_config = {
        "preprocessing": {
            "language_normalisation": {
                "provider": "bhashini",
                "supported_languages": ["hindi"],
            }
        }
    }
    llm = MagicMock()
    with pytest.raises(NotImplementedError) as exc_info:
        normaliser.normalise("kaam chahiye", bhashini_config, llm)
    assert "PoC" not in str(exc_info.value)
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cd agent_core
uv run pytest tests/test_language_normalisation.py -v -k "domain_content or default_language or short_input or min_detection or bhashini_error" 2>&1 | tail -20
```

Expected: 6 FAILED (functions not yet changed).

- [ ] **Step 3: Replace `_LANG_NORM_SYSTEM` with `_build_lang_norm_prompt()`**

In `agent_core/src/preprocessing/language_normalisation.py`, delete the `_LANG_NORM_SYSTEM` module-level constant and add this function in its place:

```python
def _build_lang_norm_prompt(supported_languages: list[str], default_language: str) -> str:
    """Build the language normalisation system prompt from config values."""
    lang_list = ", ".join(supported_languages)
    return (
        "You are a language processing assistant. Analyse the user's message and "
        "return a JSON object only — no explanation, no markdown.\n\n"
        f"Supported languages: {lang_list}\n"
        f"Default language: {default_language}\n\n"
        "Return exactly this JSON structure:\n"
        "{{\n"
        f'  "detected_language": "<one of: {lang_list}>",\n'
        '  "normalised_text": "<cleaned, normalised version of the input>"\n'
        "}}\n\n"
        "Detection rules:\n"
        f"1. When the message is short, ambiguous, or uses common words shared "
        f"across supported languages, prefer {default_language}.\n"
        "2. If the input uses Roman script for a non-Latin language (transliteration), "
        "keep it as-is — do not convert to native script.\n"
        "3. If the input mixes languages, keep the mix but clean spelling inconsistencies.\n"
        "4. Correct obvious typos only if clearly unambiguous.\n"
        "5. NEVER change the meaning or add words not present in the original."
    )
```

- [ ] **Step 4: Update `normalise()` to use the new prompt builder and add short-input bypass**

Replace the body of the `normalise()` method. The new version extracts `default_language` and `min_detection_tokens` from config and short-circuits before the LLM call when input is too short:

```python
def normalise(
    self,
    raw_input: str,
    config: dict,
    llm: LLMWrapperBase,
) -> tuple[str, str]:
    """
    Detect language and normalise input text.

    Args:
        raw_input: Original user message.
        config:    Full agent_core config dict.
        llm:       LLM wrapper for direct LLM calls.

    Returns:
        (normalised_input, detected_language)
        On any failure: (raw_input, "") — original text unchanged, no language detected.
        Never raises.
    """
    start = time.time()

    if not raw_input:
        return raw_input, ""

    block_cfg = (
        config.get("preprocessing", {})
        .get("language_normalisation", {})
    )
    supported_languages = block_cfg.get(
        "supported_languages", ["hindi", "kannada", "english", "hinglish"]
    )
    default_language = block_cfg.get("default_language", "")
    model_override = block_cfg.get("model")
    provider = block_cfg.get("provider", "llm_native")
    min_detection_tokens = int(block_cfg.get("min_detection_tokens", 3))

    try:
        if provider == "bhashini":
            raise NotImplementedError(
                "Bhashini provider is not yet implemented. "
                "Set preprocessing.language_normalisation.provider to 'llm_native'."
            )

        # Short input: classification is unreliable — return default language directly.
        if len(raw_input.split()) < min_detection_tokens:
            logger.info(
                "language_normalisation.short_input_bypass",
                extra={
                    "operation": "language_normalisation.normalise",
                    "status": "skipped",
                    "reason": "below_min_detection_tokens",
                    "latency_ms": 0,
                },
            )
            return raw_input, default_language

        system_prompt = _build_lang_norm_prompt(supported_languages, default_language)
        messages = [{"role": "user", "content": raw_input}]

        llm_response = llm.call(
            messages=messages,
            tools=[],
            system=system_prompt,
            model_override=model_override,
        )

        if llm_response.stop_reason == "error" or not llm_response.content:
            logger.warning(
                "language_normalisation.llm_failure",
                extra={
                    "operation": "language_normalisation.normalise",
                    "status": "failure",
                    "stop_reason": llm_response.stop_reason,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return raw_input, ""

        normalised, detected = self._parse_response(
            llm_response.content.strip(), raw_input
        )

        logger.info(
            "language_normalisation.normalise",
            extra={
                "operation": "language_normalisation.normalise",
                "status": "success",
                "detected_language": detected,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return normalised, detected

    except NotImplementedError:
        raise

    except Exception as e:
        logger.error(
            "language_normalisation.error",
            extra={
                "operation": "language_normalisation.normalise",
                "status": "failure",
                "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return raw_input, ""
```

- [ ] **Step 5: Run all language normalisation tests**

```bash
cd agent_core
uv run pytest tests/test_language_normalisation.py -v 2>&1 | tail -30
```

Expected: all tests PASS. If `test_model_override_passed_from_config` fails, verify config key `"model"` (not `"model_override"`) is used in `block_cfg.get("model")` — the fixture uses `"model"`, matching the schema.

- [ ] **Step 6: Commit**

```bash
cd agent_core
git add src/preprocessing/language_normalisation.py tests/test_language_normalisation.py
git commit -m "fix(language-normalisation): remove hardcoded domain prompt, weight default_language, add short-input bypass (#124)"
```

---

## Task 2: Fix language stability — prevent auto-detection from overriding session preference (Issue #123)

**Files:**
- Modify: `agent_core/src/orchestrator.py`
- Test: `agent_core/tests/test_orchestrator.py`

### Background

In both `process_turn` and `stream_turn`, the current condition is:

```python
if not saved_preference or (turn_language and turn_language != saved_preference):
    if turn_language and turn_language != saved_preference:
        language_preference = turn_language   # ← bug: overwrites preference each turn
    self._write_memory_sync(...)
```

This means any turn where the LLM detects a language different from the saved preference causes the preference to flip. A user who asks a short English question mid-session will have their preference switched to English permanently.

**Fix:** Only auto-set `language_preference` from detected language on the first turn (when `saved_preference` is empty). After that, only an explicit user switch (Task 3) may change it.

---

- [ ] **Step 1: Write the failing test**

Add to `agent_core/tests/test_orchestrator.py`:

```python
# ---------------------------------------------------------------------------
# #123 — language preference stability across turns
# ---------------------------------------------------------------------------

def test_language_preference_not_overridden_by_auto_detection():
    """When session already has language_preference, a different turn_language must not override it."""
    agent = _make_agent(
        session_data={
            "current_subagent_id": "market_truth",
            "language_preference": "hindi",
        }
    )
    # Simulate auto-detection returning "english" on this turn
    agent._language_normaliser.normalise.return_value = ("Hello", "english")

    agent.process_turn(_turn_input("Hello"))

    # Must NOT have written "english" to memory as language_preference
    for call_args in agent._memory.write.call_args_list:
        args = call_args[0]  # positional args: session_id, user_id, scope, key, value
        if len(args) >= 5 and args[3] == "language_preference":
            assert args[4] == "hindi", (
                f"language_preference was overwritten to {args[4]!r}; expected 'hindi'"
            )


def test_language_preference_set_from_detection_on_first_turn():
    """When no saved preference exists, auto-detection result becomes the preference."""
    agent = _make_agent(
        session_data={"current_subagent_id": "market_truth"}
    )
    agent._language_normaliser.normalise.return_value = ("kaam chahiye", "hindi")

    agent.process_turn(_turn_input("kaam chahiye"))

    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "persistent", "language_preference", "hindi"
    )
```

- [ ] **Step 2: Run to verify the first test fails**

```bash
cd agent_core
uv run pytest tests/test_orchestrator.py::test_language_preference_not_overridden_by_auto_detection -v 2>&1 | tail -15
```

Expected: FAILED — the orchestrator currently writes `"english"` to memory.

- [ ] **Step 3: Fix the language preference block in `process_turn`**

In `agent_core/src/orchestrator.py`, find the block starting at line ~390 (after `normalise()` call). Replace:

```python
        # Save language preference if new, or update if user switched language
        saved_preference = session_data.get("language_preference") or profile_data.get("language_preference")
        if not saved_preference or (turn_language and turn_language != saved_preference):
            if turn_language and turn_language != saved_preference:
                language_preference = turn_language
            pref_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
            self._write_memory_sync(session_id, user_id, pref_scope, "language_preference", language_preference)
            bundle.session["language_preference"] = language_preference
```

With:

```python
        # Lock in language_preference on the first turn only.
        # Explicit user switches are handled after NLU (Step 5 → language_switch_request).
        saved_preference = session_data.get("language_preference") or profile_data.get("language_preference")
        if not saved_preference:
            pref_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
            self._write_memory_sync(session_id, user_id, pref_scope, "language_preference", language_preference)
            bundle.session["language_preference"] = language_preference
```

- [ ] **Step 4: Apply the identical fix in `stream_turn`**

In `agent_core/src/orchestrator.py`, find the block starting at line ~1841. Replace:

```python
            saved_preference = session_data.get("language_preference") or profile_data.get("language_preference")
            if not saved_preference or (turn_language and turn_language != saved_preference):
                if turn_language and turn_language != saved_preference:
                    language_preference = turn_language
                pref_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
                await self._async_memory.write(session_id, user_id, pref_scope, "language_preference", language_preference)
                bundle.session["language_preference"] = language_preference
```

With:

```python
            # Lock in language_preference on the first turn only.
            # Explicit user switches are handled after NLU (Step 5 → language_switch_request).
            saved_preference = session_data.get("language_preference") or profile_data.get("language_preference")
            if not saved_preference:
                pref_scope: str = self._config.get("entity_persistence", {}).get("scope", "persistent")
                await self._async_memory.write(session_id, user_id, pref_scope, "language_preference", language_preference)
                bundle.session["language_preference"] = language_preference
```

- [ ] **Step 5: Run the new tests**

```bash
cd agent_core
uv run pytest tests/test_orchestrator.py::test_language_preference_not_overridden_by_auto_detection tests/test_orchestrator.py::test_language_preference_set_from_detection_on_first_turn -v 2>&1 | tail -15
```

Expected: both PASS.

- [ ] **Step 6: Run the full orchestrator test suite to check for regressions**

```bash
cd agent_core
uv run pytest tests/test_orchestrator.py -v 2>&1 | tail -30
```

Expected: all tests PASS. If `test_default_language_from_config_used_when_no_preference` fails, confirm it passes — it expects `"hindi"` to be written when no preference exists, which the new code still does (because `saved_preference` is empty on the first turn).

- [ ] **Step 7: Commit**

```bash
cd agent_core
git add src/orchestrator.py tests/test_orchestrator.py
git commit -m "fix(orchestrator): language_preference locked on first turn, not overridden by auto-detection (#123)"
```

---

## Task 3: Handle user-requested language switch (Issue #125)

**Files:**
- Modify: `agent_core/src/orchestrator.py` (process_turn + stream_turn)
- Modify: `agent_core/tests/test_orchestrator.py`
- Modify: `dev-kit/dev_kit/schemas/agent_core.yaml`

### Background

When a domain expert configures `language_switch_request` as an NLU intent and the NLU processor classifies the turn with that intent (with entity `requested_language`), the orchestrator must:
1. Validate the requested language is in `supported_languages`.
2. If valid — update `language_preference` in session + memory, then continue the turn normally in the switched language.
3. If invalid — return a config-driven informational message and skip the LLM.

This check lives between Step 5 (NLU entity writes, ~line 464) and Step 6 (Routing, ~line 500) in `process_turn`, and between the NLU entity writes (~line 1887) and Step 6 (~line 1889) in `stream_turn`.

The new config key `conversation.unsupported_language_message` is optional. If absent, a fallback is constructed from `supported_languages`.

---

- [ ] **Step 1: Write the failing tests**

Add to `agent_core/tests/test_orchestrator.py`:

```python
# ---------------------------------------------------------------------------
# #125 — user-requested language switch
# ---------------------------------------------------------------------------

_SWITCH_NLU = NLUResult(
    intent="language_switch_request",
    entities={"requested_language": "kannada"},
    sentiment="neutral",
    confidence=0.95,
)

_SWITCH_UNSUPPORTED_NLU = NLUResult(
    intent="language_switch_request",
    entities={"requested_language": "french"},
    sentiment="neutral",
    confidence=0.95,
)

VALID_CONFIG_WITH_LANG = {
    **VALID_CONFIG,
    "preprocessing": {
        **VALID_CONFIG.get("preprocessing", {}),
        "language_normalisation": {
            "default_language": "hindi",
            "supported_languages": ["hindi", "kannada", "english", "hinglish"],
        },
    },
    "conversation": {
        **VALID_CONFIG.get("conversation", {}),
        "unsupported_language_message": "Sorry, that language is not supported.",
    },
}


def test_language_switch_to_supported_language_updates_preference():
    """language_switch_request intent with a supported language persists the new preference."""
    agent = _make_agent(
        nlu_result=_SWITCH_NLU,
        session_data={"current_subagent_id": "market_truth", "language_preference": "hindi"},
    )
    # Override config to include supported_languages
    agent._config = VALID_CONFIG_WITH_LANG
    agent.process_turn(_turn_input("Kannada mein baat karo"))

    agent._memory.write.assert_any_call(
        SESSION_ID, SESSION_ID, "persistent", "language_preference", "kannada"
    )


def test_language_switch_to_unsupported_language_returns_config_message():
    """language_switch_request with unsupported language returns unsupported_language_message."""
    agent = _make_agent(
        nlu_result=_SWITCH_UNSUPPORTED_NLU,
        session_data={"current_subagent_id": "market_truth"},
    )
    agent._config = VALID_CONFIG_WITH_LANG
    result = agent.process_turn(_turn_input("Please respond in French"))

    assert "Sorry, that language is not supported." in result.response_text


def test_language_switch_to_unsupported_does_not_call_llm():
    """Unsupported language switch returns early without an LLM call."""
    agent = _make_agent(
        nlu_result=_SWITCH_UNSUPPORTED_NLU,
        session_data={"current_subagent_id": "market_truth"},
    )
    agent._config = VALID_CONFIG_WITH_LANG
    agent.process_turn(_turn_input("Please respond in French"))

    agent._llm.call.assert_not_called()


def test_language_switch_to_supported_language_continues_turn():
    """language_switch_request with a valid language does not short-circuit the turn."""
    agent = _make_agent(
        nlu_result=_SWITCH_NLU,
        session_data={"current_subagent_id": "market_truth"},
    )
    agent._config = VALID_CONFIG_WITH_LANG
    result = agent.process_turn(_turn_input("Kannada mein baat karo"))

    # Turn completes normally — LLM was called (via manager.run_turn) and result is non-empty
    assert result.response_text  # manager mock returns "Final response."
```

- [ ] **Step 2: Run to verify the tests fail**

```bash
cd agent_core
uv run pytest tests/test_orchestrator.py -k "language_switch" -v 2>&1 | tail -20
```

Expected: 4 FAILED.

- [ ] **Step 3: Add the language switch handler in `process_turn`**

In `agent_core/src/orchestrator.py`, locate the comment `# ── Step 6: Routing` (around line 500). Insert the following block **immediately before** that comment, after the signal writes block (the `if nlu_result.intent and nlu_result.intent in signal_intents:` block closes around line 498):

```python
        # ── Language switch — handle before routing ───────────────────
        if nlu_result.intent == "language_switch_request":
            lang_cfg = (
                self._config.get("preprocessing", {})
                .get("language_normalisation", {})
            )
            supported = [
                l.lower() for l in lang_cfg.get("supported_languages", [])
            ]
            requested_lang = (
                (nlu_result.entities or {}).get("requested_language") or ""
            ).lower().strip()

            if requested_lang and requested_lang in supported:
                pref_scope = self._config.get("entity_persistence", {}).get("scope", "persistent")
                self._write_memory_sync(session_id, user_id, pref_scope, "language_preference", requested_lang)
                bundle.session["language_preference"] = requested_lang
                detected_language = requested_lang
                logger.info(
                    "orchestrator.language_switched",
                    extra={
                        "operation": "orchestrator.language_switch",
                        "status": "success",
                        "session_id": session_id,
                        "requested_language": requested_lang,
                    },
                )
            else:
                supported_names = lang_cfg.get("supported_languages", [])
                default_msg = f"I can only respond in: {', '.join(supported_names)}."
                msg = self._config.get("conversation", {}).get(
                    "unsupported_language_message", default_msg
                )
                logger.info(
                    "orchestrator.language_switch_rejected",
                    extra={
                        "operation": "orchestrator.language_switch",
                        "status": "skipped",
                        "session_id": session_id,
                        "requested_language": requested_lang,
                        "reason": "not_in_supported_languages",
                    },
                )
                latency_ms = int((time.time() - start) * 1000)
                _trace_id = self._current_trace_id()
                turn_event = TurnEvent(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=msg,
                    tool_calls=[],
                    trust_input_result=trust_input,
                    trust_output_result=TrustCheckResult(passed=True, action="allow"),
                    model_used="",
                    intent=nlu_result.intent,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=latency_ms,
                    timestamp_ms=int(time.time() * 1000),
                    trace_id=_trace_id,
                )
                thread = threading.Thread(
                    target=self._post_turn,
                    args=(session_id, user_id, turn_id, msg, turn_input.user_message, turn_event, False, ""),
                    daemon=True,
                )
                thread.start()
                return TurnResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=msg,
                    was_escalated=False,
                    latency_ms=latency_ms,
                )
```

> **Import check:** `TurnEvent` and `threading` are already imported at the top of `orchestrator.py`. Verify with `grep -n "^import threading\|^from.*TurnEvent" agent_core/src/orchestrator.py`.

- [ ] **Step 4: Add the same handler in `stream_turn`**

In `stream_turn`, find the comment `# ── Step 6: Routing` (around line 1889). Insert the following block immediately before it, after the entity-writes loop (around line 1887):

```python
            # ── Language switch — handle before routing ───────────────
            if nlu_result.intent == "language_switch_request":
                lang_cfg = (
                    self._config.get("preprocessing", {})
                    .get("language_normalisation", {})
                )
                supported = [
                    l.lower() for l in lang_cfg.get("supported_languages", [])
                ]
                requested_lang = (
                    (nlu_result.entities or {}).get("requested_language") or ""
                ).lower().strip()

                if requested_lang and requested_lang in supported:
                    pref_scope = self._config.get("entity_persistence", {}).get("scope", "persistent")
                    await self._async_memory.write(session_id, user_id, pref_scope, "language_preference", requested_lang)
                    bundle.session["language_preference"] = requested_lang
                    detected_language = requested_lang
                    logger.info(
                        "orchestrator.language_switched",
                        extra={
                            "operation": "orchestrator.language_switch",
                            "status": "success",
                            "session_id": session_id,
                            "requested_language": requested_lang,
                        },
                    )
                else:
                    supported_names = lang_cfg.get("supported_languages", [])
                    default_msg = f"I can only respond in: {', '.join(supported_names)}."
                    msg = self._config.get("conversation", {}).get(
                        "unsupported_language_message", default_msg
                    )
                    logger.info(
                        "orchestrator.language_switch_rejected",
                        extra={
                            "operation": "orchestrator.language_switch",
                            "status": "skipped",
                            "session_id": session_id,
                            "requested_language": requested_lang,
                            "reason": "not_in_supported_languages",
                        },
                    )
                    yield TextEvent(text=msg)
                    return
```

> **Import check:** `TextEvent` is yielded by other early exits in `stream_turn`. Confirm with `grep -n "TextEvent" agent_core/src/orchestrator.py | head -5`.

- [ ] **Step 5: Add `conversation.unsupported_language_message` to the schema**

In `dev-kit/dev_kit/schemas/agent_core.yaml`, find the `conversation:` block. Add the new key after `consent_decline_ack`:

```yaml
conversation:
  blocked_message: ""           # shown when user input is blocked by Trust Layer
  escalation_message: ""        # shown when escalating conversation to a human agent
  output_blocked_message: ""    # shown when LLM output is blocked by Trust Layer
  unknown_intent_message: ""    # shown when intent cannot be classified
  termination_message: ""       # shown on session end (LLM translates to user language)
  consent_message: ""           # ask user for data storage consent
  consent_decline_ack: ""       # acknowledge when user declines consent
  unsupported_language_message: ""  # shown when user requests a language not in supported_languages
  profile_complete_message: ""  # confirm profile collection is complete
  returning_user_greeting: ""   # personalised greeting for returning users
```

Also add `min_detection_tokens` to the `preprocessing.language_normalisation` block:

```yaml
preprocessing:
  language_normalisation:
    model: ""                   # required — Claude model ID for language normalisation
    provider: llm_native        # llm_native | bhashini
    default_language: ""        # required — primary language e.g. english, hindi
    supported_languages: []     # required — e.g. [english, hindi, hinglish, kannada]
    min_detection_tokens: 3     # inputs with fewer tokens skip LLM detection; returns default_language
    transliteration: true       # normalise transliterated input to canonical script
    code_switching: true        # handle mixed-language input in a single message
```

- [ ] **Step 6: Run all new language_switch tests**

```bash
cd agent_core
uv run pytest tests/test_orchestrator.py -k "language_switch" -v 2>&1 | tail -20
```

Expected: 4 PASS.

- [ ] **Step 7: Run full orchestrator test suite**

```bash
cd agent_core
uv run pytest tests/test_orchestrator.py -v 2>&1 | tail -30
```

Expected: all tests PASS.

- [ ] **Step 8: Run all agent_core tests with coverage**

```bash
cd agent_core
uv run pytest --cov=src --cov-report=term-missing 2>&1 | tail -30
```

Expected: ≥ 70% line coverage on `src/`.

- [ ] **Step 9: Commit**

```bash
cd agent_core
git add src/orchestrator.py tests/test_orchestrator.py
git add -C .. dev-kit/dev_kit/schemas/agent_core.yaml
git commit -m "feat(orchestrator): handle language_switch_request intent with supported_languages validation (#125)"
```

> Note: if the dev-kit directory is outside the agent_core directory, stage `dev-kit/dev_kit/schemas/agent_core.yaml` from the repo root instead:
> ```bash
> git add dev-kit/dev_kit/schemas/agent_core.yaml agent_core/src/orchestrator.py agent_core/tests/test_orchestrator.py
> git commit -m "feat(orchestrator): handle language_switch_request intent with supported_languages validation (#125)"
> ```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|---|---|
| Remove hardcoded domain prompts from language_normalisation.py | Task 1 Step 3 |
| Remove bhashini domain-specific messaging | Task 1 Step 4 |
| Weight default_language in detection prompt | Task 1 Step 3 |
| Short-input bypass (configurable threshold) | Task 1 Step 4 |
| Add min_detection_tokens to schema | Task 3 Step 5 |
| Fix agent randomly switching language mid-session | Task 2 Steps 3–4 |
| First-turn auto-detection still works | Task 2 Step 1 (second test) |
| User can switch language if it's in supported_languages | Task 3 Steps 3–4 |
| Unsupported language returns config-driven message | Task 3 Steps 3–4 |
| User preference persists for the session | Task 3 Steps 3–4 |
| Add unsupported_language_message to schema | Task 3 Step 5 |

### Placeholder scan

No TBDs or placeholder steps found.

### Type consistency

- `requested_lang` is `str` throughout (lowercased immediately after extraction).
- `language_preference` and `detected_language` remain `str` throughout both process_turn and stream_turn.
- `TurnResult`, `TurnEvent`, `TrustCheckResult` — all already imported in orchestrator.py.
- `TextEvent` in stream_turn — confirm import before Step 4.
