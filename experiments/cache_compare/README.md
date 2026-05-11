# cache_compare — research harness for #311

Compares three Anthropic prompt-cache layouts (`none`, `mono`, `tiered`) across
two scenarios (`static`, `switch`) using the **live KKB prompt content** read
straight from `dev-kit/configs/kkb/agent_core.yaml`.

This is a research script. It does **not** import any DPG code — it just
mirrors the tier-assembly logic in `manager_agent.build_system_prompt()` so the
cached content is byte-identical to production.

## What it answers

1. Is `tiered` (today's prod layout) actually better than `mono` (one big
   cached block)?
2. Why does `cacheW` show ~605 tokens on every turn instead of just turn 1?
   Once `tiered + static` runs, the JSONL output makes the bug obvious — every
   row past turn 1 should have `cache_creation = 0`. Anything else = bug.

## Running it

```bash
cd experiments/cache_compare
export ANTHROPIC_API_KEY=sk-ant-...

uv sync
uv run python cache_compare.py
```

That runs all 6 cells (2 scenarios × 3 strategies × 5 turns each = 30 LLM
calls). Cheap on Haiku — a few cents end-to-end.

### Useful flags

```bash
# Just one cell:
uv run python cache_compare.py --scenarios static --strategies tiered

# Different model (e.g. Sonnet to compare cache thresholds):
uv run python cache_compare.py --model claude-sonnet-4-5-20250929

# Use the empty web suffix (reproduces the prod bug where Haiku doesn't cache):
uv run python cache_compare.py --channel web
```

## Output

- **Per-turn JSONL** to stdout (and to `results/<scenario>_<strategy>_<ts>.jsonl`).
- **Summary table** to stderr at the end with p50 latency, avg cacheR/cacheW,
  and how many of turns 2–5 actually hit the cache.

## Decision rule (apply once you have results)

| Observation in summary table | Action |
|---|---|
| `static` shows MONO ≈ TIERED on cacheR and latency | Drop tiering — simplify to one cache breakpoint. |
| `switch` shows TIERED's cacheR > 0 on the post-switch turn while MONO drops to 0 | Keep tiering — it pays off on subagent flips. |
| `tiered + static` shows `cache_creation > 0` on turns 2–5 | There's a real bug in production tier 2 — diff the bytes of tier2 across turns to find what's leaking. |
