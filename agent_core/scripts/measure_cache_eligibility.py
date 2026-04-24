"""Measure Anthropic prompt-cache eligibility for every subagent system prompt.

Loads the merged Agent Core config (dpg defaults deep-merged with the domain
YAML pointed at by ``CONFIG_FOLDER``, or the block-local ``config/domain.yaml``
fallback), assembles the system prompt exactly the way the streaming path
does — via ``ManagerAgent.build_system_prompt`` — for each subagent, and
reports whether each cached tier clears the model-specific token minimum for
Anthropic prompt caching.

Usage:
    cd agent_core
    CONFIG_FOLDER=../dev-kit/configs/kkb uv run python scripts/measure_cache_eligibility.py

Output (stdout):
    Tabular summary with chars, approx tokens, and pass/fail per model tier.

GH-219 triage aid. Not part of the production runtime.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

# Make ``src.*`` importable regardless of cwd.
_AGENT_CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENT_CORE_ROOT))

from src.manager_agent import ManagerAgent  # noqa: E402
from src.workflow_loader import WorkflowLoader  # noqa: E402


# Anthropic prompt-cache minimums (per model family). Source: Anthropic docs,
# "Prompt caching — minimum cacheable prompt length". Below these the API
# silently ignores ``cache_control`` blocks.
#
# Sonnet 3.5 / 3.7 / 4.x: 1024 tokens.
# Haiku 3.5 / 4.x:        2048 tokens.
_MIN_TOKENS_SONNET = 1024
_MIN_TOKENS_HAIKU = 2048


def _approx_tokens(text: str) -> int:
    """Rough token estimate without pulling in a tokeniser.

    Uses 4 chars/token for ASCII-dominant text and 2 chars/token for strings
    where >=30% of characters are non-ASCII (e.g. Devanagari, Kannada). The
    result is deliberately conservative — real tokenisation is within ~15%.
    """
    if not text:
        return 0
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ratio = non_ascii / len(text)
    chars_per_token = 2.0 if ratio > 0.3 else 4.0
    return int(len(text) / chars_per_token)


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, recursing into nested dicts."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_merged_config() -> dict:
    """Load dpg defaults + domain override into a single merged dict."""
    dpg_path = _AGENT_CORE_ROOT / "config" / "dpg.yaml"
    override_dir = os.environ.get("CONFIG_FOLDER")
    if override_dir:
        domain_path = Path(override_dir) / "agent_core.yaml"
    else:
        domain_path = _AGENT_CORE_ROOT / "config" / "domain.yaml"

    with dpg_path.open() as f:
        dpg = yaml.safe_load(f) or {}
    with domain_path.open() as f:
        domain = yaml.safe_load(f) or {}
    return _deep_merge(dpg, domain)


def _verdict(tokens: int, minimum: int) -> str:
    """Return PASS/FAIL tag describing whether ``tokens`` clears ``minimum``."""
    return "PASS" if tokens >= minimum else "FAIL"


def main() -> int:
    """Entry point — prints the per-subagent eligibility table."""
    config = _load_merged_config()
    workflow = WorkflowLoader.load(config)

    channel_config = {"system_prompt_suffix": ""}
    # Use a neutral channel so cache-tier shape mirrors CLI/voice.
    channel = "voice"
    profile: dict = {}  # Profile goes into tier 3 (uncached) so it's irrelevant.

    # build_system_prompt is effectively pure — it reads no instance state.
    # Call it through the class to avoid constructing the full collaborator
    # graph (LLM, registries, gateways) just to size a prompt.
    build = ManagerAgent.build_system_prompt

    agent_sp = workflow.agent_system_prompt
    # Channel rules would be in tier 1 if the domain adds a suffix — for voice,
    # we approximate with an empty string since it's not in the measurement
    # path (the sizing gap is about subagent tier 2 + the persona tier 1).

    rows: list[dict] = []
    for sa_id, sa in workflow.subagents.items():
        blocks = build(
            None,  # `self` — unused by the method body.
            agent_system_prompt=agent_sp,
            subagent_system_prompt=sa.system_prompt,
            detected_language="Hindi",
            channel=channel,
            profile=profile,
            channel_config=channel_config,
            is_resumption=False,
            guardrail_constraints=None,
            user_state_guidance=None,
            session_end_eval_prompt=None,
        )

        tier1 = blocks[0]["text"] if len(blocks) >= 1 else ""
        tier2 = blocks[1]["text"] if len(blocks) >= 2 else ""
        # Tiers 1+2 are the two cached prefixes the API will look up.
        t1_tok = _approx_tokens(tier1)
        t2_tok = _approx_tokens(tier2)
        combined_tok = _approx_tokens(tier1 + "\n\n" + tier2) if tier2 else t1_tok

        rows.append({
            "subagent_id": sa_id,
            "tier1_chars": len(tier1),
            "tier1_tokens": t1_tok,
            "tier2_chars": len(tier2),
            "tier2_tokens": t2_tok,
            "combined_tokens": combined_tok,
            "sonnet_tier1": _verdict(t1_tok, _MIN_TOKENS_SONNET),
            "sonnet_tier2": _verdict(t2_tok, _MIN_TOKENS_SONNET),
            "haiku_tier1": _verdict(t1_tok, _MIN_TOKENS_HAIKU),
            "haiku_tier2": _verdict(t2_tok, _MIN_TOKENS_HAIKU),
        })

    # ---- Print summary table ----------------------------------------
    print("\nCache-eligibility report (approx tokens; 4 chars/tok ASCII, 2 chars/tok Devanagari-heavy).")
    print(f"Sonnet min: {_MIN_TOKENS_SONNET} tok | Haiku min: {_MIN_TOKENS_HAIKU} tok\n")
    header = (
        f"{'subagent':<28} | {'t1_chars':>8} {'t1_tok':>7} {'t2_chars':>8} {'t2_tok':>7} "
        f"| Sonnet t1/t2 | Haiku t1/t2"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['subagent_id']:<28} | "
            f"{r['tier1_chars']:>8} {r['tier1_tokens']:>7} "
            f"{r['tier2_chars']:>8} {r['tier2_tokens']:>7} | "
            f"{r['sonnet_tier1']:>6}/{r['sonnet_tier2']:<6} | "
            f"{r['haiku_tier1']:>5}/{r['haiku_tier2']:<5}"
        )

    # ---- NLU system prompt measurement ------------------------------
    # NLU uses a different, flat system prompt built in nlu_processor.py.
    # We inline the template chars here so the script stays self-contained.
    from src.preprocessing import nlu_processor  # noqa: E402
    nlu_cfg = config.get("preprocessing", {}).get("nlu_processor", {})
    domain_instruction = nlu_cfg.get("domain_instruction", "")
    intents = ", ".join(nlu_cfg.get("intents", []) or ["unknown"])
    entities = ", ".join(nlu_cfg.get("entities", []) or [])
    sentiment_classes = ", ".join(
        nlu_cfg.get("sentiment_classes", []) or ["neutral", "positive"]
    )
    nlu_text = nlu_processor._NLU_SYSTEM_PROMPT_TEMPLATE.format(
        domain_instruction=domain_instruction,
        intents=intents,
        entities=entities,
        sentiment_classes=sentiment_classes,
        user_state_section="",
    )
    nlu_tok = _approx_tokens(nlu_text)
    print()
    print(
        f"NLU system prompt          | chars={len(nlu_text):>6} tokens~={nlu_tok:>6} | "
        f"Sonnet: {_verdict(nlu_tok, _MIN_TOKENS_SONNET)}  Haiku: {_verdict(nlu_tok, _MIN_TOKENS_HAIKU)}"
    )
    print(f"NLU model in config: {nlu_cfg.get('model', '<unset>')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
