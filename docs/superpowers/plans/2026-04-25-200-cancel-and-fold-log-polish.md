# #200 Cancel-and-Fold Log Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the barge-in log entry in `TurnAssembler.add_segment` from `turn_assembler.barge_in` to `turn_assembler.cancel_and_fold`, and add the two structured fields the original [#200](https://github.com/sanketika-labs/ai-diffusion-dpg/issues/200) issue called for: `cancelled_turn_id` and `folded_segment_count`.

**Architecture:** No architectural change. The functional cancel-and-fold behaviour shipped with #224's barge-in path. This plan is observability polish — making the log line match the operation that #200 documented and adding the count field future readers will look for. Single file, single commit, one new test.

**Tech Stack:** Python 3.11+, structured logging, pytest with `caplog` fixture.

**Spec context:** [#224's design spec](../specs/2026-04-25-turn-lifecycle-redesign-design.md) "§ Impact on #200" explains why #200's functional scope was absorbed into #224. This plan addresses the only delta that wasn't.

---

## Why this is the entire scope

Pre-flight reading already done — see the issue thread on PR #227 for the re-evaluation. Summary:

| #200 ask | Status after #224 |
|---|---|
| Cancel in-flight turn when new segment arrives during INVOKED | ✅ `turn_assembler.py:320-339` |
| Preserve segments as seeds for successor turn | ✅ Triggering segment becomes the seed via `replace_turn(seed_segments=[segment])` (line 341); subsequent segments accumulate on the new WAITING turn |
| `AgentCoreLLMProcessor` drops stale sentences from cancelled turn | ✅ Structural — per-turn queues mean stale sentences physically can't reach TTS. Verified by `test_session_mode_stale_sentences_appear_before_done_interrupted` |
| Acceptance test: T4–T7 replay → ≤ 1 successful DoneEvent | ✅ `test_t4_t7_replay_yields_exactly_one_completed_done` asserts exactly 1 |
| Structured log `operation=turn_assembler.cancel_and_fold` with `cancelled_turn_id`, `folded_segment_count` | ❌ This plan |

---

## File Structure

**Modified files:**
- `agent_core/src/turn_assembler.py` — rename the log call inside the `INVOKED` branch of `add_segment`. Roughly 10 lines changed.
- `agent_core/tests/test_turn_assembler.py` — add one focused log-shape test using `caplog`.

**No new files.** No public API changes. No behavioural changes.

---

## Task 1: Rename `barge_in` log to `cancel_and_fold` and add structured fields

### Files

- Modify: `agent_core/src/turn_assembler.py:316-355` (the `add_segment` `async with session._lock:` block).
- Test: `agent_core/tests/test_turn_assembler.py` (extend with one new test).

### Pre-implementation: read the current shape

The current log at `agent_core/src/turn_assembler.py:321-330` is:

```python
logger.info(
    "turn_assembler.barge_in",
    extra={
        "operation": "turn_assembler.add_segment",
        "status": "success",
        "session_id": session_id,
        "turn_id": turn.turn_id,
        "reason": "new segment arrived while INVOKED — aborting current turn",
    },
)
```

Two existing tests reference the barge-in concept by **test name** but neither asserts on the log message content (verified by `grep -n "barge_in" agent_core/tests/test_turn_assembler.py` returning only test-function names at lines 297 and 1050). So this rename is non-breaking.

### Step 1.1: Write the failing test FIRST

Append to `agent_core/tests/test_turn_assembler.py` (place it adjacent to the existing barge-in test, around line 297). Use the existing `_make_assembler`, `_make_config`, `_make_segment` helpers and the standard `caplog` pytest fixture.

```python
@pytest.mark.asyncio
async def test_cancel_and_fold_log_has_required_structured_fields(caplog):
    """When a new segment arrives during INVOKED, the cancel-and-fold log
    entry uses operation=turn_assembler.cancel_and_fold and carries
    cancelled_turn_id + folded_segment_count fields per #200."""
    import logging

    ta = _make_assembler(config=_make_config(silence_ms=5000, max_wait_ms=5000))
    await ta.add_segment("s1", _make_segment("first"))
    session = ta._sessions["s1"]
    first_turn = session.current_turn
    cancelled_turn_id = first_turn.turn_id
    # Simulate the prior turn being in flight.
    first_turn.status = TurnStatus.INVOKED
    first_turn.invocation_task = asyncio.create_task(asyncio.sleep(10))

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="src.turn_assembler"):
        await ta.add_segment("s1", _make_segment("second"))

    # Find the cancel-and-fold log record.
    fold_records = [
        r for r in caplog.records
        if getattr(r, "operation", None) == "turn_assembler.cancel_and_fold"
    ]
    assert len(fold_records) == 1, (
        f"expected exactly one cancel_and_fold log record; got {len(fold_records)}"
    )
    rec = fold_records[0]
    assert rec.msg == "turn_assembler.cancel_and_fold"
    assert rec.status == "success"
    assert rec.session_id == "s1"
    assert rec.cancelled_turn_id == cancelled_turn_id
    # Folded segment count: the triggering segment is the only seed today.
    assert rec.folded_segment_count == 1
    # The legacy "turn_id" key is no longer expected on this entry; the
    # cancelled-turn identity is conveyed by cancelled_turn_id.
    assert not hasattr(rec, "turn_id") or rec.turn_id == cancelled_turn_id
```

> Notes on the test:
> - `caplog.at_level(..., logger="src.turn_assembler")` matches the module-level `logger = logging.getLogger(__name__)` declaration in `turn_assembler.py`. Verify the logger name matches what's actually used (e.g., `src.turn_assembler` vs `agent_core.src.turn_assembler`) — read line ~50 of `turn_assembler.py` and adjust if needed. If unsure, drop the `logger=` filter and let `caplog` capture all loggers — the `operation` filter is what makes the assertion specific.
> - `imports` at the top of the test file already include `pytest`, `asyncio`, `logging` (verify; add `import logging` if missing).

### Step 1.2: Run the new test, confirm it fails

```bash
cd agent_core && uv run pytest tests/test_turn_assembler.py::TestAddSegment::test_cancel_and_fold_log_has_required_structured_fields -v
```

> If `TestAddSegment` is the wrong class name, drop the class qualifier — read the file's class structure first (around line 245+) and place the test inside the same class as `test_segment_triggers_barge_in_when_invoked`. The class name was `TestAddSegment` based on the surrounding tests in the file inventory above; verify before placing.

Expected: FAIL. `len(fold_records) == 0` because today's log uses `operation=turn_assembler.add_segment`, not `cancel_and_fold`. Or the message string is `turn_assembler.barge_in`, not `turn_assembler.cancel_and_fold`.

### Step 1.3: Apply the rename

Edit `agent_core/src/turn_assembler.py` lines 321-330. Change:

```python
logger.info(
    "turn_assembler.barge_in",
    extra={
        "operation": "turn_assembler.add_segment",
        "status": "success",
        "session_id": session_id,
        "turn_id": turn.turn_id,
        "reason": "new segment arrived while INVOKED — aborting current turn",
    },
)
```

To:

```python
logger.info(
    "turn_assembler.cancel_and_fold",
    extra={
        "operation": "turn_assembler.cancel_and_fold",
        "status": "success",
        "session_id": session_id,
        "cancelled_turn_id": turn.turn_id,
        "folded_segment_count": 1,
        "reason": "new segment arrived while INVOKED — aborting current turn",
    },
)
```

Two semantic shifts:
1. `operation` is now the action being performed (`cancel_and_fold`) rather than the calling method. This matches how other distinct events in the file are named (e.g., `turn_assembler.cancel`, `turn_assembler.session_end`).
2. `turn_id` is renamed to `cancelled_turn_id` to disambiguate from the *new* (successor) turn's id. Per the issue, `cancelled_turn_id` is the field name the operator-facing dashboards will key on.

`folded_segment_count = 1` reflects the current behaviour — the triggering segment is the only seed. This field exists for future-proofing: if/when segments arriving DURING the cancelled turn's INVOKED state get folded too, the count will rise. Not in this PR's scope.

### Step 1.4: Run the new test, confirm it passes

```bash
cd agent_core && uv run pytest tests/test_turn_assembler.py::test_cancel_and_fold_log_has_required_structured_fields -v
```

> Adjust the qualifier if the test ended up inside a class.

Expected: PASS.

### Step 1.5: Run the full TurnAssembler test file, confirm no regression

```bash
cd agent_core && uv run pytest tests/test_turn_assembler.py -v 2>&1 | tail -10
```

Expected: 77 passed (previous count + 1 new). The two existing barge-in tests (`test_segment_triggers_barge_in_when_invoked`, `test_barge_in_new_turn_uses_only_correction`) MUST still pass — they assert on state transitions, not log content, so the rename does not affect them.

### Step 1.6: Run the full agent_core suite

```bash
cd agent_core && uv run pytest -x -q 2>&1 | tail -3
```

Expected: 683 passed (was 682 + 1 new).

### Step 1.7: Commit

```bash
cd /Users/aniket/Documents/github/aniketsaki/ai-diffusion-dpg
git add agent_core/src/turn_assembler.py agent_core/tests/test_turn_assembler.py
git commit -m "$(cat <<'EOF'
fix(agent_core): rename barge-in log to cancel_and_fold with structured fields (#200)

Closes the observability gap left by #224. The functional cancel-and-fold
behaviour shipped with #224's barge-in path; this commit aligns the log
entry's operation name and adds the two structured fields the original
#200 issue specified:

- operation: turn_assembler.add_segment → turn_assembler.cancel_and_fold
- new field: cancelled_turn_id (was conflated with the generic turn_id)
- new field: folded_segment_count (currently always 1; future-proof for
  multi-segment fold)

The two existing barge-in tests assert on state transitions (status,
abort_event, segments) and are unaffected. The new caplog test verifies
the log shape directly.

Closes #200.
EOF
)"
```

---

## Self-Review Checklist

After Task 1 lands, verify:

- [ ] `grep -n "turn_assembler.barge_in" agent_core/src` returns zero hits.
- [ ] `grep -n "turn_assembler.cancel_and_fold" agent_core/src` returns exactly one hit.
- [ ] `grep -n "cancelled_turn_id\|folded_segment_count" agent_core/src/turn_assembler.py` shows both fields present.
- [ ] The two existing tests `test_segment_triggers_barge_in_when_invoked` and `test_barge_in_new_turn_uses_only_correction` are unchanged and passing.
- [ ] `cd agent_core && uv run pytest -x -q` ends with `683 passed`.
- [ ] No public API changes; signature of `add_segment` unchanged.

---

## Out of Scope

- Folding segments that arrived BEFORE the cancel-trigger (the cancelled turn's existing `.segments` list). Currently those are discarded with the cancelled turn. Folding them too would be a semantic enhancement, not a bug fix; if desired, file as a separate issue.
- Renaming the existing `test_segment_triggers_barge_in_when_invoked` test name. The test name describes a user-facing concept (barge-in) and remains accurate; the operation rename is internal observability.
- Dashboards / Grafana panels keyed off the old `turn_assembler.barge_in` event name. None exist in this repo per `grep -rn "turn_assembler.barge_in" --include="*.json"` (run before merge to confirm); if any external dashboards key off this string, update them after merge.

---

## Branch & PR

This work targets a fresh branch `fix/200-cancel-and-fold-log` cut from current `main` (which already includes #224 and #225). The existing local `fix/200-cancel-and-fold` branch was reset to main during cleanup; either reuse that branch or create a new one — either way, the diff is one commit on top of current main.

Open PR with title `fix(agent_core): rename barge-in log to cancel_and_fold with structured fields (#200)` and base `main`.
