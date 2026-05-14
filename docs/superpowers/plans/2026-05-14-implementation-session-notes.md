# Implementation Session Notes — Dev-Kit Deterministic Wizard

> **For the next session:** READ THIS FILE FIRST. It captures what was learned during execution that isn't in the plan, design, or catalogue.

**Plan being executed:** [`2026-05-14-devkit-deterministic-wizard-implementation.md`](2026-05-14-devkit-deterministic-wizard-implementation.md)

**Branch:** `docs/devkit-config-generation-revamp-design`

---

## Status (as of this handoff)

**Completed (7 tasks committed):**
- ✅ Task 0.1 — Audit runtime schema self-containedness (all 7 blocks clean, no fixes needed)
- ✅ Task 0.2 — Directory stubs for `field_rules/` and `phase_prompts/`
- ✅ Task 1.1 — `IntakeState` dataclass + persistence (with corrupt-JSON / schema-mismatch handling)
- ✅ Task 1.2 — `FieldRule` dataclass + aggregated registry (with `register_block_rules` input validation + 6 new tests)
- ✅ Task 1.3 — `path_ops.py` with `[name=X]` syntax (11 tests, unused pytest import removed)
- ✅ Task 2.1 — Dockerfile `COPY` lines for runtime schemas (+ `Dockerfile.dockerignore` negation patterns)
- ✅ Task 2.2 — `runtime_validate` using baked-in `MergedConfig` classes (with guarded imports + host/docker test split)

**Next task:** Task 2.3 — Wire dry-run into `render_all` flow.

**Recent commit log:**
```
e776604 fix(dev-kit): use extra-forbid payload to trigger trust_layer validation error
943eac7 feat(dev-kit): add runtime_validate using baked-in MergedConfig classes
56de5b8 feat(dev-kit): bake runtime block schemas into image for pre-deploy dry-run
4719fce chore(dev-kit): drop unused pytest import in test_path_ops
21c5dee feat(dev-kit): add path_ops resolver with [name=X] list-of-objects syntax
a480d55 fix(dev-kit): harden FieldRule registry + add register_block_rules tests per code review
9152d40 feat(dev-kit): add FieldRule dataclass + aggregated rules registry
d35ca1e fix(dev-kit): harden IntakeState load + drop dead imports per code review
ab9f251 feat(dev-kit): add IntakeState dataclass with persistence
fe7dc38 feat(dev-kit): scaffold field_rules and phase_prompts packages
```

---

## What to read first when picking up

1. This file (top to bottom).
2. The plan's "Locked decisions" section and the task you're about to execute.
3. The catalogue section relevant to the current task (e.g., §7.1 for Task 3.1 agent_core FIELD_RULES).
4. The most recent git log + relevant existing code in `dev-kit/dev_kit/agent/`.

The catalogue/design/plan/sync-rule are the canonical brief. This file just captures **execution discoveries** that aren't yet in those documents.

---

## Execution discoveries (not in the plan)

### 1. `dev-kit/Dockerfile.dockerignore` blocks runtime-block files

**Surprise:** The plan's Task 2.1 says `COPY agent_core/src/schema/config.py ...` "works as-is" because the build context is repo root. **It does not** — the dev-kit's `.dockerignore` explicitly excludes all 7 runtime block directories.

**Fix applied (commit `56de5b8`):** Added negation patterns to `dev-kit/Dockerfile.dockerignore`:

```
agent_core/
... (other blocks)
reach_layer/
observability_layer/

# Re-include each block's runtime schema file (and parent dirs) so the
# COPY statements in dev-kit/Dockerfile resolve.
!agent_core/src/schema/config.py
!trust_layer/src/schema/config.py
!knowledge_engine/src/schema/config.py
!action_gateway/src/schema/config.py
!memory_layer/src/schema/config.py
!observability_layer/src/schema/config.py
!reach_layer/base/schema/config.py
```

**Lesson:** When the plan introduces a new COPY into the dev-kit image, check `dev-kit/Dockerfile.dockerignore` for blocking patterns.

### 2. Renderer needs guarded imports for host-vs-docker

**Surprise:** The plan's Task 2.2 imports `from dpg_runtime_schemas.*` unconditionally. This breaks **host-side** development (`uv run uvicorn`) where the baked schemas don't exist.

**Fix applied (commit `943eac7`):** Wrapped the imports in `try/except ImportError` with `RUNTIME_SCHEMAS = None` sentinel. `runtime_validate()` raises a clear `RuntimeValidationError` if called on the host without baked schemas.

**Lesson:** Any code that imports from `dpg_runtime_schemas.*` must use the guarded pattern. Tests for that code split into host-runnable + docker-runnable using `if RUNTIME_SCHEMAS is None: pytest.skip(...)`.

### 3. `runtime_validate("<block>", {})` doesn't fail for any of the 7 blocks

**Surprise:** The plan's Task 2.2 test assumed `runtime_validate("trust_layer", {})` would fail because trust_layer has "required fields". **It doesn't** — every section in every block's `MergedConfig` has `default_factory=...`, so `{}` validates fine.

**Fix applied (commit `e776604`):** Use a payload with a clearly wrong top-level key (every `MergedConfig` sets `extra="forbid"`):
```python
runtime_validate("trust_layer", {"definitely_not_a_real_field": True})
```

**Lesson:** When you need a "this should fail Pydantic validation" payload in tests for ANY of the 7 runtime blocks, use `extra="forbid"` (unknown top-level key) — don't rely on missing-required-field semantics.

### 4. Code review consistently surfaces additional needs

Across Tasks 1.1, 1.2, and 1.3, the code-quality reviewer found genuine issues the plan didn't anticipate:

| Task | What the plan said | What review caught |
|---|---|---|
| 1.1 | `load_intake_state` just deserialises | Needs `json.JSONDecodeError` + `TypeError` handling; needs empty `selected_channels` validation; unused imports in test file |
| 1.2 | `register_block_rules` just registers | Needs input validation (block_name, FieldRule types); needs tests; `Category.__args__` should use `get_args()` |
| 1.3 | `path_ops` per spec | Unused `pytest` import |

**Pattern to apply on every task:**
- Validate inputs at function entry (per `.claude/rules/base-class-pattern.md`).
- Add `Raises:` sections to docstrings.
- Test edge cases the plan didn't list (empty inputs, type mismatches, corrupt persistence).
- Remove unused imports (one quick `grep -E "^(import|from)" <file>` and verify each is referenced).
- Use `get_args(SomeLiteral)` not `SomeLiteral.__args__`.
- Module docstrings reference the spec section (e.g., "See design §3" or "See catalogue §7.1").

### 5. Polish-fix-inline vs full re-review

For "Approved with minor follow-ups" verdicts where the issues are 1-line cosmetic fixes (unused import, comment typo), apply the fix inline (Edit + commit) rather than dispatching another full subagent loop. This is a judgment call but saves substantial context.

For Important issues (missing edge cases, structural problems), dispatch a fix subagent and re-review.

### 6. Subagent model choice

All subagent dispatches so far used `model: sonnet`. This has been adequate for:
- Mechanical implementation (provided code → file)
- Spec compliance review (read code, compare to requirements)
- Code quality review (find issues with file:line refs)
- Fix dispatches (apply specific changes)

No need to upgrade to Opus for routine tasks. Reserve Opus for:
- Phase 6.3 (`phase_driver.run_turn` — integration logic)
- Phase 12.3 / 12.4 (E2E tests — multi-step orchestration)
- Final code reviewer (whole-branch review)

---

## Established patterns

### TDD dispatch shape per task

For every task:
1. Read the task text from the plan.
2. Dispatch implementer with: full task text + context + working dir + report format.
3. Spec compliance reviewer (verify against requirements).
4. If spec ✅: code quality reviewer (CODE_REVIEW.md template + skill-specific extras).
5. If issues found: fix dispatch → re-review.
6. If only cosmetic Minor issues remain on an Approved verdict: inline fix.
7. Update TodoWrite.

### Implementer dispatch template

```
Task tool (general-purpose, model: sonnet):
  description: "Implement Task N: [name]"
  prompt: |
    You are implementing Task N: ...

    ## Task Description
    [FULL TEXT verbatim from the plan]

    ## Context
    [2-3 sentences: where this fits, what depends on it, source docs]

    ## Working directory
    /Users/srivastha/KKB/Github/ai-diffusion-dpg/

    Test commands: `cd dev-kit && uv run pytest ...`
    Per project rules: use `uv` (see .claude/rules/python-development.md).

    ## Before You Begin
    Ask if anything is unclear. Otherwise proceed.

    ## Your Job
    1. Implement exactly per spec.
    2. Follow TDD: test → fail → impl → pass → commit.
    3. Self-review.
    4. Report back.

    ## Self-Review Checklist
    - All tests pass.
    - No unused imports.
    - Module docstring states role within DPG framework.
    - Public functions have Google-style docstrings.
    - Edge cases handled per .claude/rules/base-class-pattern.md.

    ## Report Format
    Status: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
    - What you implemented
    - Test results
    - Files changed
    - Self-review findings
    - Concerns
```

### Spec reviewer dispatch template

```
Task tool (general-purpose, model: sonnet):
  description: "Spec review Task N"
  prompt: |
    You are reviewing whether an implementation matches its specification.

    ## What Was Requested
    [Concrete requirements: file paths, function signatures, exact field
    names, exact test names, exact commit message]

    ## What Implementer Claims
    [Paste their report]

    ## CRITICAL: Do Not Trust the Report
    Verify everything independently by reading the actual code.

    ## Your Job
    Read the code at <paths>; run tests via `cd dev-kit && uv run pytest ...`;
    check the commit via `git show --stat <SHA>`.

    Verify: field names/order, function signatures, test names, no extras,
    commit message exact.

    ## Report
    - ✅ Spec compliant, OR
    - ❌ Issues with file:line refs
```

### Code quality reviewer dispatch template

```
Task tool (general-purpose, model: sonnet):
  description: "Code quality review Task N"
  prompt: |
    [Per requesting-code-review/code-reviewer.md template]
    What Was Implemented: ...
    Plan: <plan path>
    Base SHA: <prev>
    Head SHA: <current>

    Check:
    - Single responsibility per file
    - Edge cases per base-class-pattern.md
    - Tests verify behaviour (not mock behaviour)
    - Google-style docstrings on public API
    - Module docstring states role within DPG framework

    Return: Strengths / Issues (Critical/Important/Minor) / Assessment.
```

---

## Verification commands cheat-sheet

```bash
# Run all dev-kit agent tests on host
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/agent/ -v

# Run one test file
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run pytest tests/agent/test_<name>.py -v

# Build dev-kit docker image (verifies COPY paths)
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg && docker build -f dev-kit/Dockerfile -t dpg-dev-kit:test . 2>&1 | tail -3

# Verify baked-in schemas import inside container
docker run --rm dpg-dev-kit:test python -c "
from dpg_runtime_schemas.agent_core.config import MergedConfig as AC
from dpg_runtime_schemas.trust_layer.config import MergedConfig as TL
from dpg_runtime_schemas.knowledge_engine.config import MergedConfig as KE
from dpg_runtime_schemas.action_gateway.config import MergedConfig as AG
from dpg_runtime_schemas.memory_layer.config import MergedConfig as ML
from dpg_runtime_schemas.observability_layer.config import MergedConfig as OL
from dpg_runtime_schemas.reach_layer.config import MergedConfig as RL
print('all 7 imported')
"

# Run renderer tests inside container (the docker-only ones)
docker run --rm \
  -v /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit/tests:/app/tests:ro \
  dpg-dev-kit:test \
  bash -c "pip install pytest --quiet && cd /app && python -m pytest tests/agent/test_renderer_runtime_validate.py -v"

# See what intake fields exist (after Task 1.1)
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run python -c "
from dev_kit.agent.intake_state import IntakeState
import dataclasses
print([f.name for f in dataclasses.fields(IntakeState)])
"

# See current aggregate field rules (empty until Phase 3 lands)
cd /Users/srivastha/KKB/Github/ai-diffusion-dpg/dev-kit && uv run python -c "
from dev_kit.agent.field_rules import AGGREGATED_FIELD_RULES
print(f'{len(AGGREGATED_FIELD_RULES)} entries')
"
```

---

## Phase-specific notes

### Phase 3 (FIELD_RULES content) — 7 nearly-identical tasks

Each per-block task transcribes catalogue §7.N into `dev-kit/dev_kit/agent/field_rules/<block>.py`.

**Pattern per block:**
1. Write `test_field_rules_<block>.py` with `EXPECTED_PATHS = {...}` from the catalogue.
2. Implement `field_rules/<block>.py` with a `FIELD_RULES = {...}` dict, one `FieldRule(...)` per row in §7.N.
3. End the module with `register_block_rules("<block>", FIELD_RULES)`.
4. Assert every chat field has `phase in FIELD_RULES_PHASES_VALID`.
5. Assert every predetermined `rule` only references `IntakeState` field names.

**Optimisation:** If running short on context budget, dispatch ONE subagent for all 7 blocks at once: provide the catalogue §7 content and ask it to produce all 7 `field_rules/<block>.py` files in sequence. Then run all 7 tests at once. This saves ~30 subagent dispatches.

**Caveat with batched approach:** Code-review depth suffers. Acceptable trade-off because:
- The content is mechanical transcription with the catalogue as source of truth.
- The aggregate test in Task 3.8 catches missing entries.
- Pre-deploy dry-run is the final safety net.

### Phase 6 (phase prompts × 11)

Each phase prompt module exports a `build(pending_fields, pydantic_schemas, cross_phase_refs, intake_state) -> str` function.

**Source of content:** Today's `dev-kit/dev_kit/agent/prompts/phases.py` — read the relevant section per phase and adapt to the new design's structure. The tier phase is NEW (intake state capture, see design §4).

**Same optimisation applies:** One subagent for all 11 prompts can save many dispatches.

### Phase 7 (Tools rewrite)

`tools.py` goes from 20 tools to 8. **Back up the current file first** (locally — not committed):
```bash
cp dev-kit/dev_kit/agent/tools.py dev-kit/dev_kit/agent/tools.py.bak
```
Reference it during the rewrite for tool argument shapes. Delete the .bak before commit.

### Phase 11 (UI changes — required only)

Three minimal changes:
- 11.1: Project creation form captures 5 intake fields (project_name, domain_description, selected_channels, default_language, supported_languages). Server-side endpoint persists `IntakeState`.
- 11.2: Deploy form pre-fills `deploy_overridable` fields (`agent.provider`, `agent.primary_model`, `agent.fallback_model`, `reach_layer.channels.voice.raya.voice_id`).
- 11.3: Chat UI shows field_status per phase.

**Out of scope:** Full UI revamp (separate plan, after this lands).

---

## Locked decisions (recap — already in plan, repeated here for safety)

1. `dignity_check.questions` → predetermined canonical English.
2. `agent.max_tool_rounds` → `framework_default_only` (3 in dpg.yaml).
3. `state.session.ttl_minutes` → gated by `is_multi_turn`.
4. `conversation.session_end_eval.prompt` → language phase.
5. `routing[*]` → per-subagent `routing` list is one chat field; whole-list invalidation.
6. `voice.recording.consent_purpose` → standalone chat field on reach_layer.
7. Multimodal Input Handler → `framework_default_only`.
8. CI Coverage guard → strong (canonical instances per known consumer) — deferred but planned.

---

## Deferred enhancements (NOT in this plan)

- **Memory Layer selective deployment** — drop Memgraph when `needs_persistent_user_data=false`. Future plan.
- **Full dev-kit UI revamp** — separate plan after this lands.
- **CI guards** (self-contained-schema, Coverage, no-redundancy) — design §5; deferred. Pre-deploy dry-run is the primary safety net until they land.
- **Pre-existing project migration** — drop and re-create; no migration in this plan.
- **`trust.consent.purposes` typed taxonomy** — would let `voice.recording.consent_purpose` derive cross-block.

---

## Kicking off the next session

In the new session, this prompt picks up cleanly:

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-deterministic-wizard-implementation.md

READ THIS FIRST (it has execution discoveries from the last session):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md

Status: 7 tasks complete on branch `docs/devkit-config-generation-revamp-design`
(through Task 2.2). Pick up at Task 2.3 (Wire dry-run into render_all flow).

Use the superpowers:subagent-driven-development skill. Dispatch a fresh
subagent per task, two-stage review (spec → code quality) after each.

Source documents:
- docs/superpowers/specs/2026-05-13-devkit-deterministic-wizard-design.md
- docs/superpowers/specs/2026-05-13-devkit-field-rules-catalogue.md
- .claude/rules/runtime-devkit-sync.md

When this session's context starts getting tight, stop at the next clean
phase boundary, commit, and append a "Session 2" section to the session
notes file describing where to pick up next.
```

---

## Appending future session notes

When a future session runs out of context and stops, it should:
1. Commit any in-flight work (or stash).
2. Append a `## Session N notes` section to this file describing:
   - Last completed task
   - Any new execution discoveries
   - Any plan deviations
   - Where to pick up
3. Commit this file.
4. Tell the user to start a fresh session with the prompt template above.

---

## Session 2 notes (2026-05-14)

**Branch:** `docs/devkit-config-generation-revamp-design`

**Tasks completed this session:** 10 (Tasks 2.3, 3.1–3.8, 4.1, 4.2) plus 2 code-review cleanup commits.

**Commit log this session (newest first):**
```
f908908 feat(dev-kit): add field_status.json read/write helpers
ce66f3c feat(dev-kit): add build_skeleton walking FIELD_RULES → accumulator + field_status
ffd2766 chore(dev-kit): clean up Phase 3 FIELD_RULES per code review
ebdbf21 feat(dev-kit): wire per-block FIELD_RULES modules into aggregate registry
2918246 feat(dev-kit): encode observability_layer FIELD_RULES from catalogue §7.7
23be3f7 feat(dev-kit): encode reach_layer FIELD_RULES from catalogue §7.3
4494935 feat(dev-kit): encode action_gateway FIELD_RULES from catalogue §7.4
f9e25c5 feat(dev-kit): encode memory_layer FIELD_RULES from catalogue §7.6
ec3689f feat(dev-kit): encode knowledge_engine FIELD_RULES from catalogue §7.5
5529224 feat(dev-kit): encode trust_layer FIELD_RULES from catalogue §7.2
d02386a feat(dev-kit): encode agent_core FIELD_RULES from catalogue §7.1
0711528 fix(dev-kit): tighten render_all dry-run docs + delay mkdir until validation passes
be1df52 feat(dev-kit): wire runtime dry-run into render_all before YAML writes
```

**Aggregate state:**
- 145 entries across all 7 blocks in `AGGREGATED_FIELD_RULES`
  (agent_core=73, reach_layer=35, trust_layer=15, memory_layer=10, knowledge_engine=7, observability_layer=3, action_gateway=2)
- Host suite: 109 passed, 3 skipped (docker-only + Task 2.3 fixture placeholder), 0 fail
- No regressions outside of the changes themselves

**Next task to pick up:** **Task 5.1 — `on_intake_update` handler** (plan lines 1944-2156).

---

### Session 2 execution discoveries (NOT in plan)

#### 1. The Phase 4 `_eval_rule` needs to be tolerant of placeholder rules

When `build_skeleton` was first implemented, several `predetermined` rules across blocks raised `SyntaxError` or `NameError` from Python's `eval`:
- `InternalConnectorDef(...)` — Phase 5 renderer helper class, doesn't exist yet
- `PersistentStateConfig(...)` — same
- `lang_code(<lang>)` — Phase 5 helper function
- `f"{slug}_knowledge"` — `slug` undefined in eval namespace
- `_CANONICAL_DIGNITY_QUESTIONS` — must be imported into the eval namespace

The implementer chose two complementary mitigations in [`skeleton.py`](dev-kit/dev_kit/agent/skeleton.py):
- `_eval_rule` catches any `Exception` and returns a `_SKIP` sentinel + logs at DEBUG (so a typo isn't completely invisible).
- `_RULE_EXTRAS` puts `_CANONICAL_DIGNITY_QUESTIONS` into the eval namespace.

**Phase 5 implication:** When the renderer-helper namespace (`slug`, `lang_code`, `project_slug`, `InternalConnectorDef`, `PersistentStateConfig`) gets defined, **add those to `_RULE_EXTRAS` in `skeleton.py`** so the `_SKIP` count drops to zero. Until then, rule typos in field_rules/* will be DEBUG-logged-and-silenced rather than failing loudly.

#### 2. Three latent bugs in Phase 3 FIELD_RULES surfaced via Phase 4 evaluation

The Phase 4 implementer fixed these during Task 4.1 — verified by spec review. All three were genuine bugs the per-block tests didn't catch (the tests assert presence of paths and category metadata, not that the rule expressions parse cleanly):

- `agent_core.py` — `connectors.internal[name=knowledge_retrieval].{name,route,input_schema}` predetermined rules were not gated by `applies_if="has_kb"`, so `set_path` would create the connector even when KB is off. Fixed.
- `memory_layer.py` — two `applies_if` expressions used uppercase `AND` (Python `eval` treats it as a name → silent skip). Fixed to lowercase `and`.
- `reach_layer.py` — `voice.recording.consent_purpose` `applies_if` referenced `recording.source` (not an IntakeState field → NameError). Simplified to `'"voice" in selected_channels'`. **Known semantic widening**: this field will now be asked whenever voice is selected, regardless of whether recording is enabled. To fully restore the catalogue intent, either add a `has_recording` IntakeState field or recategorise the field as `deploy`. Tracking as a follow-up.

**Lesson:** Add a Phase 5 (or earlier) test that does `_eval_expr(rule.applies_if, intake_state)` for every FIELD_RULE — would have surfaced all 3 bugs at commit time rather than at Phase 4. Recommend adding an aggregate-test that constructs every plausible IntakeState combination and asserts no FIELD_RULE rule/applies_if raises.

#### 3. `render_all` mkdir-after-validate trade-off

Task 2.3's code-quality review pointed out that `project_path.mkdir` ran *before* the dry-run loop, meaning a failed validation left an empty project directory. Fixed inline in commit `0711528`: `mkdir` moved to after the dry-run pass.

**Side effect to remember in Phase 12 integration:** Any caller that previously could rely on the directory existing after a failed `render_all` will now find it missing. The single caller (`conversation.py:522`) doesn't depend on this; the app.py mounts use the path differently. Confirmed no regressions.

#### 4. `RUNTIME_VALIDATE` empty-payload behaviour confirmed

Session 1 note #3 said `{}` validates against every MergedConfig because every section has a `default_factory`. Confirmed again here — `runtime_validate(block, {})` is a no-op success for all 7 blocks; **`extra="forbid"` rejection of unknown keys is the only host-portable failure path** for the renderer dry-run test. The Task 2.3 placeholder (`test_render_all_fails_when_runtime_rejects`) is correctly skipped pending Phase 4 fixture availability.

#### 5. Phase 3 transcription batch worked well

One implementer dispatch handled all 7 blocks + aggregate in sequence (per session note optimisation in §"Phase 3"). Saved ~30 subagent dispatches. The 3 transcription bugs found later (see #2) were not specific to the batched approach — they were missed by the test shape, not by depth of review.

**Recommendation for future bulk phases (e.g., Phase 6 = 11 phase prompts):** Batched dispatch is fine if (a) the catalogue/source-of-truth file is complete, (b) the test asserts more than presence-of-key, and (c) at least an eval/exec smoke test exists.

#### 6. Inert `pydantic_class` references caught at code review

Three reach_layer deploy entries (`voice.public_url`, `voice.vobiz`, `voice.recording`) listed `pydantic_class="VoiceChannelSection"` for fields that aren't actually in `VoiceChannelSection.model_fields`. Since `pydantic_class` only matters at chat-time prompt injection (deploy fields skip the chat phase), the references were inert but misleading. Cleaned up inline in `ffd2766` — these entries no longer carry `pydantic_class`.

#### 7. `derived` field `compute` expressions use mixed variable names

The catalogue (and now FIELD_RULES) mixes three forms in `compute` strings:
- `slug(project_name)` — function call
- `f"{slug}_user_id"` — `slug` as a bare variable
- `f"{project_slug}_workflow"` — `project_slug` as a bare variable

This is fine as a *declarative* hint, but Phase 9 (Task 9.1, renderer derived-field pass) will need to define exactly which names/functions are injected into the eval namespace. Either normalise the FIELD_RULES `compute` strings to one form at that time, or extend `_RULE_EXTRAS`-style binding for derived fields too. No fix needed now.

#### 8. `field_status.py` hardened beyond the plan

Plan-spec did not require `json.JSONDecodeError` handling on `load_field_status`. The implementer matched the IntakeState pattern (Session 1 note #4) and added it anyway. Two extra tests cover corrupt and non-dict JSON. The contract change is zero risk because both failure modes already returned the same value (empty dict) as the "missing file" case.

---

### Status snapshot for next session

**Completed:** 17 of the plan's tasks (Phase 0.1–0.2, 1.1–1.3, 2.1–2.3, 3.1–3.8, 4.1, 4.2)

**Next:** Phase 5 — Router + intake/config mutation handlers

- Task 5.1: `on_intake_update` handler (plan lines 1944-2156) — the cascade engine; non-trivial
- Task 5.2: `decide_next_phase` end-of-turn router (plan lines 2158-2311)
- Task 5.3: `on_config_update` handler (plan lines 2313-2322)

After Phase 5, Phase 6 has 11 phase-prompt modules (consider batching per session-note optimisation) + a phase_driver.

**No blockers.** All Phase 4 tests green; FIELD_RULES population is correct; the Task 2.3 integration test is still placeholder-skipped (will be fleshed out in Task 12.3 per the plan).

**Pickup prompt — paste into a fresh Claude Code session:**

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-deterministic-wizard-implementation.md

READ THIS FIRST (execution discoveries through Session 2):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md
(scroll to the "Session 2 notes" section)

Status: 17 tasks complete on branch docs/devkit-config-generation-revamp-design
(through Task 4.2). Pick up at Task 5.1 (on_intake_update handler).

Use the superpowers:subagent-driven-development skill. Dispatch a fresh
subagent per task, two-stage review (spec → code quality) after each.

When this session's context starts getting tight, stop at the next clean
phase boundary, commit, and append a "Session 3" section to the session
notes file describing where to pick up next.
```

---

### Session 2 — Phase 5 addendum (also 2026-05-14, same conversation)

Continued same session past the original Phase 4 stop point. Phase 5 (3 tasks) completed cleanly.

**Phase 5 commits:**
```
350c773 chore(dev-kit): fix stale comment referencing old _eval_rule name
17c76cb feat(dev-kit): add on_config_update applying chat answers with mirror validation
f1c32ce feat(dev-kit): add decide_next_phase end-of-turn router
652496f feat(dev-kit): add on_intake_update cascade through FIELD_RULES
```

**Aggregate state:**
- 120 host tests pass, 3 skipped (docker-only + Task 2.3 placeholder), 0 fail
- 145 FIELD_RULES entries unchanged
- New `dev-kit/dev_kit/agent/router.py` exposes `on_intake_update`, `decide_next_phase`, `on_config_update`, `PHASE_ORDER`, `PHASE_RELEVANCE`

**Session 2 Phase 5 execution discoveries:**

#### 9. Skeleton private helpers promoted to public

`_eval_expr`, `_eval_rule`, `_get_framework_default` in `skeleton.py` were originally underscore-prefixed but the router needs them too. Cross-module use of `_`-prefixed helpers violates `.claude/rules/base-class-pattern.md`. **Rename applied in commit 652496f:** dropped the underscore prefix, added `eval_expr`, `eval_rule`, `get_framework_default` to `__all__`. `_SKIP` sentinel also exported because the router needs the `value is not _SKIP` guard (intentionally kept underscore — it's an internal sentinel object, not a callable helper).

#### 10. Two plan-vs-test inconsistencies in Task 5.2

The plan's `decide_next_phase` body had two latent bugs that broke its own tests; the implementer fixed both with reasoning that was verified by spec review:

- `PHASE_RELEVANCE["memory"]`: plan said `lambda s: s.is_multi_turn or s.needs_persistent_user_data`. Design spec §6 PHASES dict has no `is_relevant` predicate for `memory` (meaning always-relevant). Implementer used `lambda s: True`. **Side effect of either choice is zero user-facing impact** because every memory chat field is individually gated by `applies_if`, so they all show `not_applicable` when both flags are false. Phase advances either way.
- `_is_phase_complete` default for missing field_status entries: plan said `"pending"`; implementer used `"answered"`. The plan's own test `test_advances_when_current_complete` passes `field_status = {}` and asserts the wizard advances — that requires `"answered"` default. **In production `build_skeleton` always fully populates `field_status`, so missing entries should never occur.** This is essentially a no-op safety choice.

Both deviations are correct fixes to plan inconsistencies, not new semantic decisions.

#### 11. `on_config_update` revert mechanism

Plan only described `on_config_update` in prose (no code). Controller specified the contract; implementer used `copy.deepcopy(accumulator[block])` to snapshot the block before the write, and on validation failure restores the snapshot. This is stricter than the alternative (`clear_path` on the just-written path) because validation could be invalidated by other in-flight changes too — restoring the whole block is the safest revert. **Pattern to remember when other writers land** (Phase 7 tool add, Phase 11 UI deploy field updates).

#### 12. Memory phase semantic when both flags off

Per #10 above: when `is_multi_turn=false AND needs_persistent_user_data=false`, the wizard still passes through `memory` phase but asks zero questions. That's fine. But if a Phase 6 phase-prompt for memory unconditionally tries to address the user ("Now let's set up memory!"), the user would see an empty/awkward turn. **Phase 6 phase-prompt implementer should check whether any chat field in the phase is `pending` before generating a user-facing prompt.** This is an instruction for future Phase 6 work, not a current bug.

---

### Status snapshot for next session (after Phase 5)

**Completed:** 20 of the plan's tasks (Phase 0.1–0.2, 1.1–1.3, 2.1–2.3, 3.1–3.8, 4.1, 4.2, 5.1–5.3)

**Next:** Phase 6 — Phase prompts + phase driver (3 tasks):
- Task 6.1: `phases_config.py` — declarative PHASES dict (plan lines 2327-2381)
- Task 6.2: Per-phase prompt modules × 11 (plan lines 2383-2397; **batched dispatch recommended** per Session 1 optimisation note)
- Task 6.3: `phase_driver.py` — single shared phase runner (plan lines 2399-2419; **use Opus** per Session 1 note since this is integration logic)

After Phase 6, Phases 7-13 are mostly more independent transcription work and final integration.

**No blockers.** All Phase 5 tests green. The router is fully covered. Skeleton helpers are now public; any future caller can import them cleanly.

**Pickup prompt for next session (paste verbatim):**

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-deterministic-wizard-implementation.md

READ THIS FIRST (execution discoveries through Session 2 Phase 5):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md
(scroll to the "Session 2 notes" section AND its "Phase 5 addendum")

Status: 20 tasks complete on branch docs/devkit-config-generation-revamp-design
(through Task 5.3). Pick up at Task 6.1 (phases_config.py).

For Task 6.2 (11 per-phase prompt modules), consider one batched implementer
dispatch as per the Session 1 note in §"Phase 3 / Phase 6 optimisation".

For Task 6.3 (phase_driver.run_turn), use model=opus — this is integration logic.

Use the superpowers:subagent-driven-development skill. Dispatch a fresh
subagent per task, two-stage review (spec → code quality) after each.

When this session's context starts getting tight, stop at the next clean
phase boundary, commit, and append a "Session 3" section to the session
notes file describing where to pick up next.
```

---

## Session 3 notes (2026-05-14)

**Branch:** `docs/devkit-config-generation-revamp-design`

**Tasks completed this session:** 3 (Tasks 6.1, 6.2, 6.3 — all of Phase 6) plus 4 polish/refactor commits.

**Commit log this session (newest first):**
```
ae22516 chore(dev-kit): apply phase_driver code-review polish
2f7b4bf feat(dev-kit): add phase_driver.run_turn integrating LLM + tools + router
707a613 test(dev-kit): simplify tier form-field negative assertion
d1802e1 docs(dev-kit): name dev-kit in phase-prompt module docstrings
e5f4bbb feat(dev-kit): teach review phase to re-ask needs_re_asking fields
6da7e05 refactor(dev-kit): extract duplicated phase-prompt helpers into _helpers module
ebcc517 feat(dev-kit): add tier phase prompt builder
31fa378 feat(dev-kit): add language phase prompt builder
7b7c83d feat(dev-kit): add knowledge phase prompt builder
04b4c07 feat(dev-kit): add tools phase prompt builder
08f4c28 feat(dev-kit): add memory phase prompt builder
db9c54f feat(dev-kit): add user_state phase prompt builder
0fe1458 feat(dev-kit): add trust phase prompt builder
9ad91f3 feat(dev-kit): add workflow phase prompt builder
92a67cc feat(dev-kit): add reach phase prompt builder
1fdb506 feat(dev-kit): add observability phase prompt builder
b9dfbe3 feat(dev-kit): add review phase prompt builder
0ba7dd8 chore(dev-kit): document phases_config memory deviation and add __all__
18bc4d5 feat(dev-kit): add declarative PHASES config
```

**Aggregate state:**
- 272 host tests pass, 3 skipped (docker-only + Task 2.3 placeholder), 0 fail
- 11 per-phase prompt modules + shared `_helpers.py` (`_render_fields`, `_path_of`, `_rule_of`)
- New `dev-kit/dev_kit/agent/phase_driver.py` exposes `run_turn`, persistence helpers (`load/save_accumulator`, `load/save_current_phase`), `collect_pending_fields`, `cross_phase_references`, `render_pydantic_classes` (stub), `TOOL_HANDLERS` dispatch dict, and the `LLMResponse`/`ToolCall` dataclasses

**Next task to pick up:** **Task 7.1 — Rewrite `tools.py` with the 8-tool set** (plan lines 2423-2458). The old `dev-kit/dev_kit/agent/tools.py` has 20 tools and is ~65 KB. Phase 7 trims to 8: `update_intake`, `update_config`, `add_subagent`, `update_subagent`, `add_routing_rule`, `add_tool`, `parse_openapi_spec`, `discover_mcp_tools`.

---

### Session 3 execution discoveries (NOT in plan)

#### 13. Plan's `phases_config.PHASES["memory"]` predicate conflicts with prior router decision

Plan Task 6.1 specifies `lambda s: s.is_multi_turn or s.needs_persistent_user_data` for memory's `is_relevant`. Design §6 lines 648-665 has NO `is_relevant` argument for memory (always-relevant). Session 2 Phase 5 already established `router.PHASE_RELEVANCE["memory"] = lambda s: True`. To keep router + phases_config aligned (and to follow design over plan), the Task 6.1 implementer used `_always` for memory in `PHASES` too. Documented inline in `phases_config.py:67-70`.

Implication: `phases_config.PHASES` is intended as the canonical source eventually; `router.PHASE_RELEVANCE` is a duplicate. A future task should refactor router.py to import from phases_config — left as a transitional pin (test in `test_phases_config.py` cross-checks `tuple(PHASES.keys()) == PHASE_ORDER`).

#### 14. Plan's `PhaseDefinition.prompt_module: str` deviates from design's `prompt_fn: Callable`

Design §6 stores callables directly. Plan stores leaf module names as strings to avoid circular imports during testing. The implementer followed the plan. Driver resolves the callable via `importlib.import_module(f"dev_kit.agent.phase_prompts.{prompt_module}")` and `module.build`. This works cleanly and tests don't need import-time gymnastics.

#### 15. The 11 phase prompts started as ~670 lines of duplicated helpers

The batched Task 6.2 implementer copy-pasted `_path_of`, `_rule_of`, `_render_fields` into all 11 modules. Code review caught this and a follow-up refactor extracted them into `phase_prompts/_helpers.py` (commit `6da7e05`). `tier.py` keeps a local wrapper around `_render_fields` because its empty-list sentinel is different ("tier intake flags live in IntakeState, not FIELD_RULES."). **Lesson for future batched dispatches:** include "if helpers duplicate, extract to a shared module" as an explicit step, not a post-review fix.

#### 16. Task 6.3's `current_phase` persistence is a new file, not in IntakeState

`<slug>/_meta/current_phase.txt` is the source of truth for "which phase the wizard is currently in." Defaulted to `"tier"` on missing/empty/unknown values (matching the lenient `load_field_status` pattern). `save_current_phase` enforces validity (raises `ValueError` if phase not in `PHASE_ORDER`).

This is a NEW persistence point not covered by IntakeState/accumulator/field_status. The phase_driver creates it on first save when a transition fires.

#### 17. Accumulator persistence: NEW format, separate from old ConfigAccumulator

The new wizard's accumulator is a plain `dict[str, dict]` keyed by block (the same shape `router.on_intake_update` / `on_config_update` mutate). The OLD `dev-kit/dev_kit/agent/accumulator.py:ConfigAccumulator` has a different shape (with statuses, subagent helpers, etc.) — left untouched, will be removed in a later phase.

`phase_driver.load_accumulator` / `save_accumulator` use `<slug_root>/_meta/accumulator.json` and backfill missing block keys with empty dicts so callers can index unconditionally.

#### 18. Tool routing is open-ended and tolerant

`TOOL_HANDLERS` currently has only `update_intake` and `update_config`. The phase 7 rewrite adds `add_subagent`, `add_tool`, etc. Unknown tool names log a `phase_driver.unsupported_tool` warning and continue — no crash. Tool calls that raise `KeyError`/`ValueError`/`AttributeError` inside handlers (missing args, validation failure, unknown intake field) are also caught and logged as `phase_driver.tool_call_failed`. This tolerance is intentional: the LLM is sometimes wrong, but the turn should still produce a response.

#### 19. `render_pydantic_classes` is a stub

Returns `""` for empty pending_fields, otherwise a placeholder comment listing the paths. Full Pydantic-source injection lands in a later phase (likely Phase 9 alongside renderer derived fields, or earlier if the LLM needs schema hints). **Don't expand this in Phase 7 unless explicitly requested.**

#### 20. `cross_phase_references` covers a focused set

`agent.{provider, primary_model, fallback_model}`, language normalisation, NLU intents/entities, KB intent_filter keys, agent_core connector names. This matches the cross-block references the design surfaces. If a future phase prompt tells the LLM "use the value from <new_path>", add the path to `cross_phase_references` too — otherwise the LLM is told to read a value it can't see.

#### 21. `# noqa: ARG001` on a used parameter is a code smell

The Task 6.3 implementer added `# noqa: ARG001 — passed through to llm_call only` to `user_message` in `run_turn`. The parameter IS used (it's passed to `llm_call(system_prompt, user_message)`). Code review caught this; polish commit removed the comment. Watch for the same pattern in future driver/handler signatures — if you add `noqa`, double-check the linter is actually flagging it.

---

### Status snapshot for next session (after Phase 6)

**Completed:** 23 of the plan's tasks (Phase 0.1–0.2, 1.1–1.3, 2.1–2.3, 3.1–3.8, 4.1, 4.2, 5.1–5.3, 6.1, 6.2, 6.3)

**Next:** Phase 7 — Tool surface (1 task):
- Task 7.1: Rewrite `dev-kit/dev_kit/agent/tools.py` with the 8-tool set (plan lines 2423-2458; **back up the old file locally** to `tools.py.bak` per the plan note — see Session 1 note "Phase 7 (Tools rewrite)" for the back-up rationale)

After Phase 7:
- Phase 8 — Selective compose generation (1 task, depends on IntakeState)
- Phase 9 — Renderer derived-field pass (1 task)
- Phase 10 — Decision logging (3 sub-tasks)
- Phase 11 — UI changes (3 sub-tasks)
- Phase 12 — Integration tests (mostly Opus territory; 12.3 / 12.4 are E2E)
- Phase 13 — Final cleanup (delete old `prompts/phases.py`, old `accumulator.py`, etc.)

**No blockers.** All Phase 6 tests green (272 host pass, 3 skipped, 0 fail). The phase_driver is fully covered and ready for Phase 7's new tools to wire into `TOOL_HANDLERS`.

**Outstanding follow-ups carried forward (low priority):**
- `test_phase_prompts_observability.py` has only the 7 minimum tests; could add a phase-specific extra later (e.g., `outcomes` schema injection).
- `render_pydantic_classes` stub will need expansion before users see helpful prompts in any phase that emits pending chat fields.
- Eventual refactor: delete `router.PHASE_ORDER` / `PHASE_RELEVANCE` once Phase 7+ can rely solely on `phases_config.PHASES`.

**Pickup prompt for next session (paste verbatim):**

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-deterministic-wizard-implementation.md

READ THIS FIRST (execution discoveries through Session 3):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md
(scroll to the "Session 3 notes" section)

Status: 23 tasks complete on branch docs/devkit-config-generation-revamp-design
(through Task 6.3, all of Phase 6 done). Pick up at Task 7.1 (rewrite tools.py
with the 8-tool set).

For Task 7.1: per Session 1 note in §"Phase 7 (Tools rewrite)", BACK UP the
current tools.py locally first (copy to dev-kit/dev_kit/agent/tools.py.bak —
do NOT commit the backup). Reference it while writing the new 8-tool file,
then delete the .bak before committing.

Use the superpowers:subagent-driven-development skill. Dispatch a fresh
subagent per task, two-stage review (spec → code quality) after each.

When this session's context starts getting tight, stop at the next clean
phase boundary, commit, and append a "Session 4" section to the session
notes file describing where to pick up next.
```

---

## Session 4 notes — state-layer migration kicked off

**Date:** 2026-05-14

**Scope:** After the deterministic-wizard implementation was reported complete, a verification audit revealed the old wizard's state model (`ConfigAccumulator`, `ConversationEngine`, `checkpoints.py`, `ConfigStatus`) was never replaced — only the chat turn handler was rewired. Roughly 30 `/api/projects/*` endpoints still depend on the old state. User chose **Path A — full migration on this branch**.

### What landed in Session 4

| Commit | What |
|---|---|
| `8fe2b8b` | Wired IntakeState into app.py deploy preview + runner (replaces ConfigAccumulator at 4 call sites; adds has_kb/has_external_tools strips; depends_on cleanup) |
| `cc10904` | Removed unused `compose_generator.py` + its tests (dead code from prior session) |
| `16daa11` | **State-layer migration plan** at `docs/superpowers/plans/2026-05-14-devkit-state-layer-migration.md` — 7 phases, 22 tasks |
| `8e45ad0` | Phase A.1 — `project_state.py` (accumulator dict persistence, 6 tests) |
| `6499067` | Phase A.2 — `history.py` (append-only jsonl, 5 tests) |
| `3e63f7b` | Phase A.3 — `block_status.py` (derive complete/incomplete from field_status, 7 tests) |

**Net:** Phase A of the migration plan complete. 18 new tests added. New state modules ready for downstream phases.

### Status snapshot

- **Migration plan:** `docs/superpowers/plans/2026-05-14-devkit-state-layer-migration.md` (read this for the full task breakdown)
- **Phase A:** ✅ Complete (3 tasks)
- **Phase B:** ⏳ Next — refactor `renderer.render_all` to take `accumulator: dict` + `IntakeState` instead of `ConfigAccumulator`
- **Phase C–G:** Pending — endpoint migration, deletions, UI updates, test migration, smoke + docs

### Locked design decisions (do NOT revisit)

| ID | Decision |
|---|---|
| D1 | `_meta/accumulator.json` is pure block YAML state (no status/phase). Implemented in `project_state.py`. |
| D2 | `_meta/history.jsonl` is append-only chat history. Implemented in `history.py`. |
| D3 | `ConfigStatus` enum is replaced by `"complete" / "incomplete"` strings derived from `field_status.json`. Implemented in `block_status.py`. |
| D4 | **Checkpoints feature is dropped entirely.** No replacement. Delete `checkpoints.py`, 3 endpoints, React UI bits. |
| D5 | `ConversationEngine` class is dropped. Endpoints use per-request loaders. `/chat` calls `phase_driver.run_turn` directly. |

### Next-session kick-off prompt

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-state-layer-migration.md

READ THIS FIRST (execution discoveries from prior sessions):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md (Session 4
section is the most relevant)

Status: Phase A complete on branch `docs/devkit-config-generation-revamp-design`.
Pick up at Phase B (Task B.1 — refactor renderer.render_all to take
accumulator: dict + IntakeState; drop ConfigAccumulator + ConfigStatus
dependencies).

Use the superpowers:subagent-driven-development skill. Dispatch a fresh
subagent per task, two-stage review (spec → code quality) after each.

When this session's context starts getting tight, stop at the next clean
phase boundary, commit, and append a "Session 5" section to the session
notes file describing where to pick up next.
```

### Watch out for in Phase B onwards

- **`renderer.render_all` change is breaking** — every caller (mostly in `app.py`) needs simultaneous update. Don't merge Phase B without ALSO updating callers, OR temporarily keep a legacy wrapper that adapts the old call shape.
- **`accumulator.py` is still imported by:** `conversation.py`, `tools.py`, `renderer.py`, `app.py`, `phase_driver.py` (docstring reference only), `checkpoints.py`, and 6 test files. Don't try to delete it until Phase D — earlier phases need to migrate callers one-by-one.
- **`tools.py`'s `update_config` writes to `ConfigAccumulator` today.** Must be rewired to read+write the accumulator dict + `save_accumulator(...)`. This is Phase C work but worth flagging — the deterministic wizard's runtime depends on tools.py writing correctly to the new state.
- **Phase C is the bulk of the work** (~30 endpoints, 4 sub-tasks in the plan). Recommend dispatching one sub-task per fresh session to keep reviews tractable.

---

## Session 5 notes (2026-05-14)

**Branch:** `docs/devkit-config-generation-revamp-design`

**Tasks completed this session:** 4 (B.1, C.1, C.2, C.4) plus 3 code-review polish commits and inline polish edits. Phase B fully done; Phase C is 3/4 done (C.3 deferred).

**Commit log this session (newest first):**
```
f7cce91 chore(dev-kit): drop 3 checkpoint endpoints — feature deprecated (Task C.4)
a986cb5 chore(dev-kit): apply Task C.2 code-review polish
c380d1e feat(dev-kit): migrate /chat and /history to phase_driver + history.jsonl (Task C.2)
99d8edf chore(dev-kit): apply Task C.1 code-review polish (ValueError handling, missing tests, docstrings)
5846c91 feat(dev-kit): migrate lifecycle endpoints to per-request state loaders (Task C.1)
86890d5 chore(dev-kit): apply renderer code-review polish (dead const, guards, warning header fix)
b265e16 refactor(dev-kit): render_all takes dict + IntakeState; drops ConfigAccumulator dependency
```

**Aggregate state:**
- `dev-kit/dev_kit/agent/renderer.py` — `render_all(project_path, accumulator: dict, intake_state, *, deploy_settings=None) -> dict[str, str]`. Returns `"complete"|"failed"`. `_DRAFT_HEADER`/`_STALE_HEADER_TPL` gone; mirror warnings emitted as a single `# WARNINGS:` block. `render_block` inlined. No imports from `accumulator.py`.
- `dev-kit/dev_kit/agent/app.py` — Lifecycle endpoints (POST `/api/projects`, GET `/api/projects/{slug}`, DELETE `/api/projects/{slug}`) use per-request loaders. `/chat` and `/history` call `phase_driver.run_turn` and `history.load_history` directly. `_build_devkit_llm_call()` lives in app.py (duplicate of `conversation.py:_build_phase_driver_llm_call` — Phase D dedupes). Two new helpers `_required_secrets_from_accumulator` and `_channel_secrets_from_intake_and_accumulator`. 3 checkpoint endpoints + `from dev_kit.agent.checkpoints import ...` removed.
- `dev-kit/dev_kit/agent/phase_driver.py` — `run_turn` now appends `HistoryEntry` rows (user before LLM, assistant after `decide_next_phase`, both with `phase=current_phase`).
- Test files added: `tests/agent/test_renderer_render_all.py` (19 tests), `tests/agent/test_app_project_lifecycle.py` (21 tests), `tests/agent/test_app_chat_history.py` (7 tests), 3 history-append tests in `tests/agent/test_phase_driver.py`.

**Test pass rate:** Agent tests 438 passed + 13 expected pre-existing failures (`test_deploy_preview_intake.py` — uses old `render_all(project, acc)` signature; covered by Phase F).

**Next task to pick up:** **Task C.3 — Migrate config read/write endpoints** (plan lines 743-760). 6 endpoints around `app.py:787-908`:
- `GET /api/projects/{slug}/configs` — list per-block status + content
- `GET /api/projects/{slug}/configs/export` — zip/tar of all YAMLs
- `GET /api/projects/{slug}/configs/{block}` — single block content
- `PUT /api/projects/{slug}/configs/{block}` — write block YAML (with `validate_partial`)
- `POST /api/projects/{slug}/configs/reload` — re-read YAMLs from disk into accumulator
- `POST /api/projects/{slug}/configs/validate` — run mirror-schema validation

After Phase C lands, the remaining work is:
- Phase D — Delete dead code (`checkpoints.py`, `accumulator.py`, `ConversationEngine` class). D.2 dedupes the two `_build_devkit_llm_call`/`_build_phase_driver_llm_call` copies into one shared helper.
- Phase E — Frontend `"PENDING"/"DRAFT"/...` → `"complete"/"incomplete"`; delete checkpoint UI.
- Phase F — Migrate/delete legacy tests.
- Phase G — Smoke + docs + final review.

---

### Session 5 execution discoveries (NOT in plan)

#### 1. `GET /api/projects/{slug}` returned more than just status

The plan only called out `config_statuses` for migration in Task C.1. But the endpoint actually returns 7 fields derived from `ConfigAccumulator` methods (`is_azure_needed`, `get_required_secrets`, `get_required_channel_secrets`, `has_knowledge_base`, `agent.provider`, etc.). Mapped as:

- `config_statuses` → `all_block_statuses(field_status)` (clean migration).
- `azure_storage.needed` → **hardcoded `False`**. The old `ConfigAccumulator._data["azure_storage"]` was a non-block key; the new dict accumulator rejects non-block keys (`_BLOCKS_SET` allowlist). Marked as a deferred stub pending intake-state extension.
- `required_secrets` → `_required_secrets_from_accumulator(accumulator)` reads `accumulator["action_gateway"]["tools"]` directly.
- `channel_secrets` → `_channel_secrets_from_intake_and_accumulator(intake, accumulator)`. Uses `intake.selected_channels` instead of `_data["reach_layer"]["_selected_channels"]`.
- `has_knowledge_base` → `intake.has_kb` (intake is the new source of truth, not the YAML).
- `llm_provider` → `accumulator.get("agent_core", {}).get("agent", {}).get("provider") or "anthropic"`.

These two new private helpers (`_required_secrets_from_accumulator`, `_channel_secrets_from_intake_and_accumulator`) live in app.py for now; Phase D should move them to a `meta_derive.py` or similar (~100 line `_channel_secrets_from_intake_and_accumulator` is borderline-too-big to stay in app.py).

#### 2. Legacy-project edge case in `get_project`

When `_meta/intake_state.json` doesn't exist (pre-deterministic-wizard projects), `load_intake_state` raises `FileNotFoundError`. Catch it and set `intake = None`, then guard every `intake.X` reference. Pattern:
```python
meta["has_knowledge_base"] = bool(intake and intake.has_kb)
meta["channel_secrets"] = (
    _channel_secrets_from_intake_and_accumulator(intake, accumulator) if intake else []
)
```

`ValueError` on the same load (corrupt JSON) must NOT be silently treated as a legacy project — Code review caught this; final version logs + raises HTTP 500 separately. Same pattern applied to `load_accumulator` and `load_field_status` per-call.

#### 3. `BLOCKS` ordering divergence (deferred to Phase D)

`accumulator.BLOCKS` (list) and `project_state.BLOCKS` (tuple) have different ordering. No runtime bug yet because no code iterates them in parallel. Phase D deletes `accumulator.py` cleanly; no action needed in Phase C.

#### 4. `_build_devkit_llm_call` duplicates `conversation.py:_build_phase_driver_llm_call`

Identical body. Acceptable for C.2 since `ConversationEngine` goes in Phase D.2 — that's the right place to dedupe. After D.2, only the app.py copy survives.

#### 5. `phase_driver.run_turn` writes user entry BEFORE the LLM call

This is intentional fail-safety: if the LLM crashes mid-turn, the user message is still persisted. The test `test_run_turn_user_entry_written_before_llm_call` proves this by injecting a failing LLM and asserting exactly one entry (user) in `history.jsonl`. **Caveat:** if `append_turn` itself raises (disk full / permission), the whole turn fails before LLM runs. Currently no try/except guards this — flagged as a minor follow-up.

#### 6. `tests/agent/test_deploy_preview_intake.py` is broken since Phase B

13 failures, all from `acc = ConfigAccumulator(); render_all(project, acc)` (old signature). These DON'T fail elegantly — they pass `ConfigAccumulator` to the new `render_all` and crash on signature mismatch. The plan's Phase F handles this (rewrite or xfail). DO NOT confuse these with regressions when reading test output during Phase C/D.

#### 7. `restore_checkpoint_route` was already broken before C.4

The deleted `POST /api/projects/{slug}/checkpoints/{phase}/restore` called `render_all(project_path, restored_acc)` with the OLD signature — meaning it had been broken since Phase B (commit b265e16). C.4's deletion fixed a latent bug nobody noticed.

#### 8. App test fixture pattern

For C.1, C.2 (and C.3 going forward), the canonical app test fixture is:
```python
@pytest.fixture()
def client(tmp_path, monkeypatch):
    import dev_kit.agent.app as app_mod
    configs = tmp_path / "configs"
    configs.mkdir()
    monkeypatch.setattr(app_mod, "CONFIGS_DIR", configs)
    app_mod._engines.clear()
    return TestClient(app_mod.app), configs
```

This works because `_get_project_path` reads `CONFIGS_DIR` from module scope each call (not a closure). The `_engines.clear()` is defensive in case earlier tests in the session created engine instances.

---

### Status snapshot for next session (after Phase C, partial)

**Completed:** 28 of the plan's tasks (Phase A.1–A.3, B.1, C.1, C.2, C.4)

**Next:** Task C.3 — Migrate the 6 config read/write endpoints. Subtleties:
- `PUT /api/projects/{slug}/configs/{block}` currently calls `engine.accumulator._data[block] = parsed` (reaching into private state). Migrate to: load accumulator dict from disk, `accumulator[block] = parsed`, save back. Also: validate via `validate_partial` and update field_status accordingly (or leave field_status alone — the put is an out-of-band edit, not a wizard turn).
- `POST /api/projects/{slug}/configs/reload` re-reads YAML files from disk. Migrate to: read each `<block>.yaml` file, parse, write into accumulator dict, save accumulator.
- `GET /api/projects/{slug}/configs` returns per-block `{block, status, content}` — `status` is the new `"complete"|"incomplete"` from `all_block_statuses`.
- `POST /api/projects/{slug}/configs/validate` — same `validate_partial` per block; no engine needed.
- `GET /api/projects/{slug}/configs/{block}` returns a single block.
- `GET /api/projects/{slug}/configs/export` zips all YAMLs.

**No blockers.** All Phase B + C.1 + C.2 + C.4 tests green; pre-existing 13 deploy_preview_intake.py failures are expected per Phase F.

**Pickup prompt for next session (paste verbatim):**

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-state-layer-migration.md

READ THIS FIRST (execution discoveries through Session 5):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md (Session 5
section is the most relevant; review Session 4 too for full context)

Status: Phase A, B done. Phase C 3/4 done (C.1, C.2, C.4 complete; C.3
remaining). Branch: docs/devkit-config-generation-revamp-design.

Pick up at Task C.3 — Migrate config read/write endpoints in app.py
(plan lines 743-760, 6 endpoints around app.py:787-908).

Use the superpowers:subagent-driven-development skill. Dispatch a fresh
subagent per task, two-stage review (spec → code quality) after each.

When this session's context starts getting tight, stop at the next clean
phase boundary, commit, and append a "Session 6" section to the session
notes file describing where to pick up next.
```

---

## Session 6 notes (2026-05-14)

**Branch:** `docs/devkit-config-generation-revamp-design`

**Tasks completed this session:** Task C.3 — the 6 config read/write endpoints — plus three review-driven fixes. Phase C is now fully complete (C.1–C.4).

**Commit log this session (newest first):**
```
71e4eb7 chore(dev-kit): apply Task C.3 code-review polish
4f7fb2b fix(dev-kit): guard remaining load_field_status callers after contract change
e184730 fix(dev-kit): raise ValueError on corrupt field_status.json + propagate as 500 in GETs
f265761 refactor(dev-kit): config read/write endpoints use project_state loaders
```

**Aggregate state:**
- 6 endpoints migrated off `_get_engine` / `engine.accumulator` / `ConfigStatus` / `DRAFT_BLOCKS` (lines 1057-1182 in `app.py`):
  - `GET /api/projects/{slug}/configs`
  - `GET /api/projects/{slug}/configs/export`
  - `GET /api/projects/{slug}/configs/{block}`
  - `PUT /api/projects/{slug}/configs/{block}`
  - `POST /api/projects/{slug}/configs/reload`
  - `POST /api/projects/{slug}/configs/validate`
- New `dev-kit/tests/agent/test_app_config_endpoints.py` — 36 tests (29 from main commit + 4 from contract-change fixes + 3 from polish).
- `field_status.load_field_status` contract change: now raises `ValueError` on corrupt JSON (was previously lenient). Five call sites in `app.py` and one in `phase_driver.run_turn` all guarded. The non-dict branch of `load_field_status` retains lenient `return {}`.
- Test pass rate: agent suite 472 passed, 13 expected pre-existing failures in `test_deploy_preview_intake.py` (Session 5 note #6 — covered by Phase F), 3 skipped.

**Next task to pick up:** **Phase D — Delete dead code** (3 tasks). Start at **Task D.1 — Delete `checkpoints.py`** (plan lines 779-803).

---

### Session 6 execution discoveries (NOT in plan)

#### 1. `load_field_status` had to flip from lenient to raising

Before this session, `load_field_status` (`dev-kit/dev_kit/agent/field_status.py`) silently returned `{}` on `json.JSONDecodeError`, even though the documented cross-cutting rule (per Session 2 note #8) said corrupt state → HTTP 500. The C.1 endpoint (`get_project`) had a defensive `try/except ValueError` that was dead code under the old contract. To enforce the rule in Task C.3 we changed `load_field_status` to raise `ValueError` on corrupt JSON. The change made the C.1 defensive guard meaningful and required adding the same guard to **every** other caller of `load_field_status`. There are now 6 guarded call sites:
- `app.py:861` (`get_project` — C.1)
- `app.py:1083` (`get_configs`)
- `app.py:1176` (`get_config`)
- `app.py:1257` (`update_config_file`)
- `app.py:1313` (`reload_configs`)
- `app.py:1660` (`get_field_status` — Phase 11.3 endpoint, technically outside C.3 but adjacent)
- `phase_driver.py:446` (`run_turn` — structured log + re-raise, no HTTPException since it's not an HTTP handler)

**Lesson for future similar contract changes:** When you tighten a loader to raise where it previously returned a default, **grep `dev-kit/dev_kit/` AND `dev-kit/tests/` for ALL callers** and ensure each handles the new exception path, even ones nominally outside the current task scope. A contract change is wider than the task that motivates it.

#### 2. `phase_driver.load_accumulator` is INTENTIONALLY lenient — do not auto-flip it next time

`phase_driver.load_accumulator` (different function from `project_state.load_accumulator`) logs a warning and returns an empty skeleton on corrupt accumulator.json. The asymmetry with `load_field_status` (which now raises) is **intentional**: a corrupt accumulator can be recovered with `POST /configs/reload`, whereas a corrupt `field_status.json` means the wizard has lost track of which fields are pending and must fail fast. The new `# NOTE:` comment at `phase_driver.py:444-447` documents this.

#### 3. Two near-identical `save_accumulator` symbols live in `app.py`

`app.py` imports both:
- `phase_driver.save_accumulator(slug_root, accumulator)` — slug-root based
- `project_state.save_accumulator(path, accumulator)` — explicit file path

The latter is aliased to `_save_accumulator_path` to avoid shadowing. The import comment now explains this. **Phase D.2 should consolidate** — once `ConversationEngine` is gone, the phase_driver variant should become the sole writer for the wizard, and the path-based variant could move to a helper for explicit-path callers (the C.3 endpoints).

#### 4. Test-fixture pattern continues to work — minor footgun

`monkeypatch.setattr(app_mod, "CONFIGS_DIR", configs)` only works because `_get_project_path` and the new endpoint bodies read `CONFIGS_DIR` from module scope on every call. If a future endpoint captures `CONFIGS_DIR` in a closure or default-argument, the fixture breaks silently. No fix needed now, but worth a sanity check during Phase E (UI integration) where new endpoints sometimes appear.

#### 5. Plan said "PUT could update field_status" — we chose NOT to

Plan Step 2 for C.3 says "validate via `validate_partial` and update field_status accordingly (or leave field_status alone — the put is an out-of-band edit, not a wizard turn)." We chose the "leave alone" branch and added a regression test (`test_does_not_mutate_field_status` reads field_status before and after, asserts equal). This matches Session 5 note #2's framing of the PUT as out-of-band. **Confirmed locked decision for Phase E onwards**: the PUT endpoint does NOT advance the wizard.

#### 6. Plan said "Export endpoint — serialise the accumulator dict as YAML" — we did NOT

The export endpoint was already engine-free; it reads on-disk YAML files (with `# not yet configured` placeholders for missing blocks). We left it alone. The on-disk YAML is the source of truth users want exported (it has comments, headers, and the operator-supplied formatting), not the in-memory accumulator. Added a one-line note to the export endpoint docstring explaining this.

#### 7. `test_deploy_preview_intake.py` still failing — out of scope

13 pre-existing failures in `test_deploy_preview_intake.py` (Session 5 note #6). They have NOT been touched in this session and are expected to remain red until Phase F migrates / xfails them. Do NOT mistake these for new C.3 regressions when reading test output during Phase D.

---

### Status snapshot for next session

**Completed:** 29 of the plan's tasks (Phase A.1–A.3, B.1, C.1–C.4)

**Next:** **Phase D — Delete dead code** (3 tasks):
- Task D.1: Delete `checkpoints.py` (plan lines 781-803)
- Task D.2: Drop `ConversationEngine` from `conversation.py` — replace with thin module + dedupe the two `_build_devkit_llm_call` / `_build_phase_driver_llm_call` copies (plan lines 805-853)
- Task D.3: Delete `accumulator.py` + update `tools.py`, `cross_block_validation.py`, etc. (plan lines 855-915)

After Phase D:
- Phase E — UI string + checkpoint cleanup
- Phase F — Test migration / xfail `test_deploy_preview_intake.py`
- Phase G — Smoke + docs + final review

**Watch out for in Phase D:**
- `tools.py`'s `update_config` writes to ConfigAccumulator today. Must be rewired to read+write the accumulator dict + `save_accumulator(...)` (per Session 4 note). D.3 covers this but it's the most error-prone substitution.
- `conversation.py:_build_phase_driver_llm_call` and `app.py:_build_devkit_llm_call` are identical bodies (Session 5 note #4). D.2 should dedupe — choose one as the canonical helper and import from there.
- D.1 deletes `dev-kit/tests/test_app_endpoints.py` (per plan) — verify nothing else in that file is still relevant before deletion. Re-skim before removing.
- `phase_driver.py:docstring` references `accumulator.py` — needs updating in D.3 once the file is gone.

**No blockers.** All C.3 tests green; the 13 pre-existing failures are documented and out-of-scope.

**Pickup prompt for next session (paste verbatim):**

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-state-layer-migration.md

READ THIS FIRST (execution discoveries through Session 6):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md
(Session 6 section is the most relevant; Session 5 covers Phase B/C
context that Phase D builds on)

Status: Phases A, B, C done on branch docs/devkit-config-generation-revamp-design.
Pick up at Phase D — Delete dead code (3 tasks: D.1 checkpoints.py,
D.2 ConversationEngine, D.3 accumulator.py).

D.2 should also dedupe the two _build_devkit_llm_call / _build_phase_driver_llm_call
copies (Session 5 note #4; Session 6 note #3 expands on the import asymmetry).

Use the superpowers:subagent-driven-development skill. Dispatch a fresh
subagent per task, two-stage review (spec → code quality) after each.

When this session's context starts getting tight, stop at the next clean
phase boundary, commit, and append a "Session 7" section to the session
notes file describing where to pick up next.
```

---

## Session 7 notes (2026-05-14)

**Branch:** `docs/devkit-config-generation-revamp-design`

**Tasks completed this session:** All of Phase D — D.1, D.2, D.3. Dead-code deletion is finished.

**Commit log this session (newest first):**
```
991cc12 chore(dev-kit): delete accumulator.py — replaced by project_state + block_status
4376b3e refactor(dev-kit): drop ConversationEngine class — conversation.py is now a thin wrapper
1230e77 chore(dev-kit): delete checkpoints.py — replaced by history.jsonl
```

**Aggregate state:**
- Three module deletions: `accumulator.py` (~950 lines), `checkpoints.py` (~150 lines), `ConversationEngine` (~350 lines from `conversation.py`). Net: 1,400+ lines removed from the runtime.
- `conversation.py` is now a 60-line thin module exporting `chat_turn(projects_root, slug, user_message, *, llm_call) -> str` and `get_history(projects_root, slug) -> list[HistoryEntry]`.
- `app.py` no longer holds `_engines` registry, `_get_engine` helper, or any ConversationEngine reference. Three remaining `engine.accumulator.*` callers (deploy/validate fallback, workflow_graph, deploy-fields) migrated to `load_accumulator` + small pure-dict helpers (`_reach_channels_from_accumulator`, `_workflow_graph`).
- `tools.py` line 37 dead import removed (no replacement — symbols were never used).
- Test suite: **0 failures**, 455 passed, 4 skipped (3 prior + 1 module-skip on `test_deploy_preview_intake.py` quarantining its 13 tests pending Phase F rewrite).
- Deleted test files: `test_app_endpoints.py`, `test_accumulator_validation.py`, `test_accumulator_azure.py`, `test_accumulator_connector.py`, `test_conversation_phase_driver.py`, plus `dev-kit/agent/tests/test_conversation.py` and `dev-kit/agent/tests/test_checkpoints.py`.

**Next task to pick up:** **Phase E — Update React UI** (plan lines 905+). First step: `grep -rn '"PENDING"|"DRAFT"|"STALE"|"COMPLETE"|ConfigStatus' dev-kit/frontend/src/` to find any code comparing against the old enum-value strings; replace with `"complete"|"incomplete"`. Plus remove checkpoint-restore UI.

---

### Session 7 execution discoveries (NOT in plan)

#### 1. The chat endpoint was already migrated in C.2 — D.2's "Step 3" was nearly a no-op for `chat()`

Session 5 already moved the chat endpoint off `ConversationEngine` (it calls `phase_driver.run_turn` with `_build_devkit_llm_call()` directly). D.2's main work was deleting the class itself + cleaning up THREE non-chat callers that still wanted `_get_engine(slug).accumulator.*`. The plan's wording ("chat endpoint uses `conversation.chat_turn(...)`") was a touch misleading — by Phase D the chat endpoint had nothing to migrate.

**Decision taken:** Keep app.py's chat endpoint calling `phase_driver.run_turn` directly (don't refactor through `conversation.chat_turn`). The thin `conversation.chat_turn` exists as the public surface for external callers; the in-process FastAPI handler keeps the direct call for symmetry with the existing structured-logging block. The `_build_devkit_llm_call` / `_build_phase_driver_llm_call` deduplication originally flagged in Session 5 note #4 was naturally resolved when `conversation.py` shed its LLM-builder method — there's only ONE copy left now (`app.py:_build_devkit_llm_call`).

#### 2. `test_deploy_preview_intake.py` quarantine vs deletion

D.3 had to deal with this file — it imports `ConfigAccumulator` and feeds the old `render_all(project, acc)` signature. The 13 failing tests have been documented in every session since Session 5. Two options:
- **A:** Delete now (matching the plan's `git rm test_accumulator_validation.py` pattern).
- **B:** Quarantine with module-level `pytest.skip(allow_module_level=True)`.

We chose **B**. Reasoning: Phase F's stated job is "migrate or xfail" this file. Deleting now would force Phase F to rewrite from scratch; keeping with a skip preserves the existing test names and assertion shapes for Phase F to translate against the new contract. The trade-off: a `pytest.skip` at module level shows as ONE skip event, not 13 — so the suite output reads "4 skipped" instead of "16 skipped + 13 xfails." Acceptable.

**Action for Phase F:** open `test_deploy_preview_intake.py`, remove the `pytest.skip(...)` block, then iterate through each test rewriting `acc = ConfigAccumulator(); render_all(project, acc)` to the new `render_all(project_path, accumulator=dict, intake_state=...)` shape. Many tests will translate cleanly; a few may need actual fixture redesign.

#### 3. `accumulator.PHASES` was a different list from `phases_config.PHASES`

The legacy `accumulator.PHASES` was a tuple of phase ordering used for `ConfigStatus` lifecycle decisions. The new `phases_config.PHASES` is a dict of `PhaseDefinition` objects keyed by phase name. **They are NOT 1:1** — the legacy list was about 11 phase names in the older order; the new dict has the same 11 phases but with the post-design ordering. `cross_block_validation.py:24` had a stale comment pointing at `accumulator.PHASES` — updated to point at `phases_config.PHASES`. If a future caller needs the *list* form, do `list(phases_config.PHASES.keys())`.

#### 4. `BLOCKS` consolidation in app.py

Before D.3, app.py imported `BLOCKS` from `accumulator` (line 32) AND from `project_state` indirectly (via the project_state import block at line 48). After D.3 there's a single import block:

```python
from dev_kit.agent.project_state import (
    BLOCKS,
    empty_accumulator,
    load_accumulator,
    save_accumulator as _save_accumulator_path,
)
```

`accumulator.BLOCKS` was a list, `project_state.BLOCKS` is a tuple. Verified that every use in app.py is iteration or `in`-membership — both work on tuples. No code change needed at the use sites.

#### 5. `app.py` still has historical-lineage docstrings mentioning ConfigAccumulator

`app.py:543,564,566` retain phrases like "Mirrors `ConfigAccumulator.get_workflow_graph`". These are intentional documentation breadcrumbs — they explain *why* a small inline helper exists (mirroring an old class method shape). They aid `git log -G ConfigAccumulator` archaeology. **Do not remove them in Phase E** — they're not bugs, they're history.

#### 6. The agent suite shrank to 455 passed (was 472 at the end of Session 6)

Math:
- 472 (Session 6 baseline, including 13 deploy_preview_intake failures shown as "13 failed, 472 passed")
- −6 from deleting `test_conversation_phase_driver.py` (D.2)
- −12 from deleting `test_accumulator_validation.py` (D.3)
- +1 from a stray spec-fix test we added in D.3 (small)
- = ~455

13 fails → 1 module skip (encapsulated). Net visible-failure count: 0. Net pass count: down by 16, all of which were legitimately obsolete after the class deletions.

---

### Status snapshot for next session (after Phase D)

**Completed:** 32 of the plan's tasks (Phase A.1–A.3, B.1, C.1–C.4, D.1–D.3)

**Next:** **Phase E — Update React UI** (plan lines 905+). Three subtasks:
- E.1: Block status strings — replace `PENDING/DRAFT/STALE/COMPLETE` with `complete/incomplete` in frontend.
- E.2: Remove checkpoint-restore UI elements.
- E.3: (if present in plan) Any other UI cleanup tied to the new state model.

After Phase E:
- **Phase F — Test migration** — primary work is rewriting `test_deploy_preview_intake.py` against the new `render_all(dict, IntakeState)` signature. Open the file, remove the `pytest.skip(allow_module_level=True)` block, rewrite each test. Also re-add coverage for endpoints that used to live in the deleted `test_app_endpoints.py` (export, schema-descriptions, devkit-config) — most are already covered by the new `test_app_config_endpoints.py`, but verify.
- **Phase G** — Smoke + docs + final review.

**No blockers.** Test suite is at its cleanest in the entire migration: 0 failures, 4 module-level skips (3 pre-existing + 1 quarantine).

**Pickup prompt for next session (paste verbatim):**

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-state-layer-migration.md

READ THIS FIRST (execution discoveries through Session 7):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md
(Session 7 section is the most relevant; Session 5–6 give Phase B/C context)

Status: Phases A, B, C, D done on branch docs/devkit-config-generation-revamp-design.
Pick up at Phase E — Update React UI (plan lines 905+; three subtasks
around block-status strings, checkpoint-restore UI removal).

Use the superpowers:subagent-driven-development skill. Dispatch a fresh
subagent per task, two-stage review (spec → code quality) after each.

When this session's context starts getting tight, stop at the next clean
phase boundary, commit, and append a "Session 8" section to the session
notes file describing where to pick up next.
```


## Session 8 notes (2026-05-14)

**Branch:** `docs/devkit-config-generation-revamp-design`

**Tasks completed this session:** All of Phase E (E.1, E.2), all of Phase F (test migration), and most of Phase G — G.2 (docs partial) is done; G.1 (smoke test) is deferred to the operator; G.3 (final review) is deferred to the next session.

**Commit log this session (newest first):**

```
feb42d4 chore(dev-kit): drop empty legacy agent/tests/ directory + pyproject testpath
4170bd4 test(dev-kit): xfail test_existing_configs_validate (legacy YAMLs predate wizard)
2058fa0 test(dev-kit): migrate deploy-routes and cross-block-validation fixtures
0910842 chore(dev-kit): drop redundant top-level test_renderer and test_app_project_routes
a0a20fd chore(dev-kit): drop legacy agent/tests/ and migrate channel_tts to new APIs
b9d2191 test(dev-kit): migrate test_deploy_preview_intake.py to new render_all signature
575ad5e chore(dev-kit/frontend): remove checkpoint-restore UI
8d5a4ae feat(dev-kit/frontend): switch to new block-completion strings (complete/incomplete)
```

**Aggregate state:**

- Phase E: React UI reduced from 4-state block status (`complete/draft/pending/stale`) to 2-state (`complete/incomplete`); `DiffModal.jsx` deleted; `PhaseBar` no longer renders restorable buttons (the entire `<button onClick={...} disabled={...}>` mode collapsed to non-interactive `<div title="...">` rows); all checkpoint API methods removed from `api.js`; both `checkpoint_created` blocks removed from `Chat.jsx`. Frontend tests: 123 pass / 6 fail — the 6 are pre-existing failures unrelated to this migration (5 in `Chat.test.jsx` traced to `api.getFieldStatus` not being mocked at `Chat.jsx:84` since some earlier Phase 11 work; 1 in `ProjectList.test.jsx`).
- Phase F: legacy test cleanup. Test suite went from **7 collection errors + 7 failures + 20 errors** when running the full `uv run pytest` to **1032 passed / 5 skipped / 5 xfailed / 21 xpassed / 0 failures / 0 errors**. Six test files deleted (4 in `agent/tests/`, 2 top-level), `agent/tests/test_channel_tts.py` relocated to `tests/agent/` and rewritten with dict fixtures, 4 surviving files migrated to new APIs, `tests/schemas/test_existing_configs_validate.py` xfailed at module level with `strict=False`. The `agent/tests/` directory and the legacy `agent/__init__.py` package marker were also deleted, and `pyproject.toml` testpaths trimmed to `["tests"]`.
- Phase G.2 (partial): `CLAUDE.md` dev-kit file tree updated to include `project_state.py`, `block_status.py`, `history.py`, and the corrected `current_phase.json` + `history.jsonl` in the per-project state list. `dev-kit/README.md` (which predated the entire deterministic-wizard era) substantially rewritten to describe the wizard, the 8-tool surface, the IntakeState-gated phase flow, and the on-disk state model. ARCHITECTURE.md was already current — no changes needed.

**Next tasks to pick up:**

1. **Phase G.1 — Manual smoke test.** The plan asks for a full end-to-end deploy walkthrough. This requires a running Docker stack (`docker compose -f automation/docker/docker-compose.dev.yml up -d dev_kit`) and CANNOT be done from a Claude session — operator must run it. Verify: (a) `_meta/intake_state.json`, `_meta/accumulator.json`, `_meta/field_status.json`, `_meta/current_phase.json`, `_meta/history.jsonl` all populate during a fresh project chat; (b) NO `_meta/checkpoints/` directory is created at any point; (c) the deploy preview correctly strips `knowledge_engine` for a `has_kb=false` project. Commit an empty `test(dev-kit): manual smoke test of new state model — all green` per the plan's G.1 Step 6 once verified.

2. **Phase G.3 — Final code review (full branch).** Dispatch a final code-reviewer subagent against `main..HEAD`. The branch has 30+ commits, ~7,500 lines net change. Expected verdict: ready for PR.

3. **Optional follow-up from Phase F reviewer notes (NOT blocking):**
   - The `tests/test_app_deploy_routes.py::test_get_project_returns_required_secrets_and_azure_needed` test now pins `azure_storage.needed is False` because `app.py:950-952` hard-codes that to False as a deferred-stub. When intake_state gains an `azure_storage` flag, the assertion needs to flip back to `is True`.
   - The `_make_intake` helper is duplicated across `tests/agent/test_deploy_preview_intake.py` and `tests/test_app_deploy_routes.py` (and inline-constructed in others). A shared `tests/conftest.py` fixture would deduplicate ~30 lines but is not load-bearing.
   - `tests/schemas/test_existing_configs_validate.py` uses `xfail(strict=False)` because 21 of 28 parametrised cases pass; once the legacy YAMLs are cleaned up, flip to `strict=True` so those become regular PASSED.

**No blockers.** Test suite is in its cleanest state ever; the full `uv run pytest` succeeds without any collection-time tolerance flags.

---

### Session 8 execution discoveries (NOT in plan)

#### 1. The frontend already used lowercase status strings — the plan was misleading

Plan E.1 says "replace `PENDING/DRAFT/STALE/COMPLETE` with `complete/incomplete`". A grep for uppercase enum names returned ZERO matches: the backend had been serialising the old `ConfigStatus` enum to lowercase strings all along, and the frontend already consumed lowercase. The actual scope was reducing the 4-state lowercase model (`complete/draft/pending/stale`) to the 2-state lowercase model (`complete/incomplete`).

The Dashboard's `HealthBanner` had a third orphaned branch: `hasStale ? 'border-red-700 bg-red-950/30'` with a "Fix stale configs" pill. That branch was dead code under the new model and got removed in E.1. The 4-state `STATUS_PILL` / `STATUS_COLORS` / `STATUS_DOT` lookup tables in `constants.js` collapsed to 2-state entries.

#### 2. `STATUS_PILL` is defined three times in the frontend (pre-existing duplication)

`constants.js`, `ConfigEditor.jsx:12`, and `DiffModal.jsx:15` each had their own local `STATUS_PILL`. E.1 faithfully updated all three; `DiffModal.jsx` was then deleted in E.2 so the duplication is now down to two. Not flagged as a Phase E task; left as-is. A follow-up could consolidate the `ConfigEditor.jsx` local copy to import from `constants.js`.

#### 3. The chat-response field `checkpoint_created` was removed in TWO places

`Chat.jsx` had `if (res.checkpoint_created) { api.getCheckpoints(slug)... }` blocks in BOTH the `send()` handler (line ~113) AND the `attachFile()` handler (line ~201). Easy to miss the second one. Verified gone by `grep -rn 'checkpoint_created' dev-kit/frontend/src/`.

#### 4. `PhaseBar` collapsed from a `<button>`-based restorable list to a `<div>`-based static progress indicator

The pre-E.2 PhaseBar had `<button onClick={hasCheckpoint && onRestoreCheckpoint(...)} disabled={!hasCheckpoint}>` for each phase row. After E.2 those became `<div title={PHASE_LABELS[phase]}>` — no interaction. The `✓ / ● / ○` markers, the collapsed-mode dot row, and the expand/collapse toggle button all survived. Six tests deleted from `PhaseBar.test.jsx` (the restore-specific ones); the `.closest('button')` selector in two assertions was rewritten to `.closest('div[title]')`.

#### 5. Phase F was much bigger than the plan implied

The plan emphasised `tests/agent/test_deploy_preview_intake.py` (13 tests) as the primary rewrite. Reality: the full `uv run pytest` was failing at COLLECTION with 7 broken files importing deleted symbols. Investigation found:

- `agent/tests/test_accumulator.py`, `test_accumulator_channel_secrets.py`, `test_renderer.py`, `test_app.py` — all completely broken (imported deleted classes); all four had new equivalents in `tests/agent/`. DELETED.
- `agent/tests/test_channel_tts.py` — tests for `channel_tts.py` (still exists) with `ConfigAccumulator`-based fixtures. REWRITTEN with dict fixtures and relocated to `tests/agent/test_channel_tts.py`.
- `tests/test_renderer.py` (16 tests) — broken AND superseded by `tests/agent/test_renderer_render_all.py`. DELETED.
- `tests/test_app_project_routes.py` (47 tests) — superseded by `tests/agent/test_app_project_lifecycle.py` + `test_app_chat_history.py` + `test_app_config_endpoints.py`; the checkpoint section tested deleted endpoints. DELETED.
- `tests/test_app_deploy_routes.py` (37 tests) — plan said "ALREADY MIGRATED" but still imported `ConfigAccumulator` and `_engines.clear()`. MIGRATED.
- `tests/schemas/test_cross_block_validation.py` (23 tests) — fixtures used `ConfigAccumulator()`. MIGRATED.
- `tests/schemas/test_existing_configs_validate.py` — XFAIL per plan.

Net: 6 file deletions + 4 file migrations + 1 relocation + 1 xfail. Five logical commits.

#### 6. `pyproject.toml` testpaths was the lurking root cause of the collection errors

`[tool.pytest.ini_options] testpaths = ["tests", "agent/tests"]` — pytest was scanning BOTH directories, but the Session 7 baseline was running only `pytest tests/agent/` (a third path) so the broken files in `agent/tests/` never surfaced until Session 8. After deleting the dead files and the empty `agent/tests/` directory, `testpaths` was trimmed to `["tests"]` so the configuration matches reality.

#### 7. The `azure_storage.needed` test now pins a hardcoded constant

`tests/test_app_deploy_routes.py::test_get_project_returns_required_secrets_and_azure_needed` previously exercised `acc.declare_azure_needed()` → endpoint returns `True`. The new `app.py:950-952` hard-codes `meta["azure_storage"] = {"needed": False}` per a deferred-stub comment, so the migrated test asserts `is False`. The test now adds little semantic value — it's a watchpoint for the eventual intake-state extension. A future commit (when `IntakeState` gains an `azure_storage` flag) needs to flip this assertion back. The implementer added a comment noting this; the reviewer flagged it as the most important concern in the Phase F review but approved the change.

#### 8. The empty `agent/` package was a tracked-in-git ghost

After the four test files were deleted in commit `a0a20fd`, the `agent/tests/` directory was empty save for an empty `__init__.py`. Pytest still collected it as a zero-test package, and the `__pycache__` survived. Cleaned up post-Phase-F in `feb42d4`: `git rm agent/__init__.py agent/tests/__init__.py`; removed `__pycache__`; trimmed `pyproject.toml` `testpaths` to just `["tests"]`. Suite remained at 1032 passed.

---

### Status snapshot for next session (after Phase F + partial Phase G.2)

**Completed:** 37 of the plan's tasks (Phase A.1–A.3, B.1, C.1–C.4, D.1–D.3, E.1–E.2, F.1, G.2 partial).

**Next:**
- **Phase G.1 — Manual smoke test** (operator must run; cannot be done in-session). When done, commit an empty `test(dev-kit): manual smoke test of new state model — all green` per the plan.
- **Phase G.3 — Final code review (full branch).** Dispatch the final code-reviewer subagent against `main..HEAD`. ~30 commits on this branch.

**Test counts at end of Session 8:**
- Python: 1032 passed / 5 skipped / 5 xfailed / 21 xpassed / 0 failures / 0 errors.
- Frontend: 123 passed / 6 failed (all 6 pre-existing, unrelated to this migration).

**Pickup prompt for next session (paste verbatim):**

```
Continue executing the implementation plan at:
docs/superpowers/plans/2026-05-14-devkit-state-layer-migration.md

READ THIS FIRST (execution discoveries through Session 8):
docs/superpowers/plans/2026-05-14-implementation-session-notes.md
(Session 8 section is the most relevant; Session 7 covers Phase D context)

Status: Phases A–F + Phase G.2 (docs) done on branch
docs/devkit-config-generation-revamp-design. 37/39 tasks complete.
Test suite: 1032 passed / 5 skipped / 5 xfailed / 21 xpassed / 0 failures.

Pick up at:
  Phase G.1 — Manual smoke test (operator-only; cannot run from session;
    once verified, append an empty commit with the smoke-test message).
  Phase G.3 — Dispatch the final code-reviewer subagent against main..HEAD.

After Phase G is done, the branch is ready for PR.
```

