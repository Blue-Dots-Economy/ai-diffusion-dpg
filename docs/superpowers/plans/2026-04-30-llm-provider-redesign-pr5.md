# LLM Provider Redesign — PR5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Delete the legacy `llm_wrapper/` adapter and `LLMResponse` dataclass; pin every shipped domain config to `agent.provider: anthropic`; update `CLAUDE.md`, `ARCHITECTURE.md`, and `agent_core/README.md` to reflect the new architecture. After PR5 the redesign is complete and the integration branch is ready to merge to `main`.

**Architecture:** This is the cleanup PR. Nothing inside `agent_core/` consumes `LLMWrapperBase`/`ClaudeLLMWrapper`/`LLMResponse` after PR4 (verified). PR5 removes them. Domain configs already-deployed run unchanged because `agent.provider` defaults to `"anthropic"` on the schema; the explicit value is added for documentation and to make the choice visible.

**Tracking:** Parent #287; resolves #292. Branch `pr5/cleanup-and-docs` off `feature/llm-provider-redesign`.

**Spec:** `docs/superpowers/specs/2026-04-30-llm-provider-redesign-design.md`

---

## File structure

```
DELETE:
  agent_core/src/llm_wrapper/                  # entire package (3 files: __init__.py, base.py, claude_wrapper.py)
  agent_core/tests/test_llm_wrapper.py         # adapter unit tests
  agent_core/tests/test_llm_wrapper_caching.py # adapter caching tests

MODIFY:
  agent_core/src/models.py                     # remove LLMResponse dataclass
  agent_core/src/orchestrator.py               # drop the unused LLMResponse import + stale comment
  agent_core/README.md                         # rewrite the LLM-wrapper paragraph + diagram
  CLAUDE.md                                    # point at chat_provider/, drop wrapper line
  ARCHITECTURE.md                              # short paragraph on chat_provider
  dev-kit/configs/kkb/agent_core.yaml
  dev-kit/configs/docs-assistant/agent_core.yaml
  dev-kit/configs/obsrv-docs-assistant/agent_core.yaml
  dev-kit/configs/the-blue-dots-economy/agent_core.yaml
```

---

## Conventions

- Branch every commit on `pr5/cleanup-and-docs`. Verify with `git branch --show-current`.
- Test command: `cd agent_core && uv run pytest`. Final regression must show the same baseline (1 pre-existing kkb failure, no new ones).
- Coverage gate (`fail_under=70`) must still pass.

---

## Task 1: Branch off

- [ ] **Step 1:** `git fetch origin && git checkout -b pr5/cleanup-and-docs origin/feature/llm-provider-redesign`

---

## Task 2: Drop the unused `LLMResponse` import in orchestrator.py

**Files:**
- `agent_core/src/orchestrator.py`

The PR3 migration left two stragglers: an unused `LLMResponse` import (line 60) and a stale code comment mentioning `LLMResponse` (around line 2655). Drop both.

- [ ] **Step 1:** Remove `LLMResponse,` from the `from src.models import (...)` block at the top of orchestrator.py (line 60).
- [ ] **Step 2:** Find the stale `# LLMResponse.` comment around line 2655. If the surrounding comment block can be rewritten naturally (e.g. "Build a synthetic ChatResponse for ..."), rewrite it. If the comment is just a leftover, delete the line.
- [ ] **Step 3:** `cd agent_core && uv run pytest tests/test_orchestrator.py -x` → all pass.
- [ ] **Step 4:** Commit:

```bash
git add agent_core/src/orchestrator.py
git commit -m "$(cat <<'EOF'
chore(orchestrator): drop unused LLMResponse import + stale comment (#292)

Cleanup left over from the PR3 migration. orchestrator.py no longer
constructs or returns LLMResponse, so the import is dead.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Delete `llm_wrapper/` package and its tests

**Files:**
- DELETE: `agent_core/src/llm_wrapper/__init__.py`
- DELETE: `agent_core/src/llm_wrapper/base.py`
- DELETE: `agent_core/src/llm_wrapper/claude_wrapper.py`
- DELETE: `agent_core/tests/test_llm_wrapper.py`
- DELETE: `agent_core/tests/test_llm_wrapper_caching.py`

- [ ] **Step 1:** Verify nothing imports the package outside itself:

```bash
grep -rn "from src.llm_wrapper\|import src.llm_wrapper\|src/llm_wrapper" agent_core/src/ agent_core/tests/ | grep -v __pycache__
```

Expected: no production imports outside the package itself; only `tests/test_llm_wrapper.py` and `tests/test_llm_wrapper_caching.py` reference it (both being deleted).

If a stray import surfaces in a non-test file, STOP and report — that's a missed migration from PR3/PR4 that needs to land first.

- [ ] **Step 2:** Delete the package and the tests:

```bash
rm -rf agent_core/src/llm_wrapper/
rm agent_core/tests/test_llm_wrapper.py agent_core/tests/test_llm_wrapper_caching.py
```

- [ ] **Step 3:** Run the full suite:

```bash
cd agent_core && uv run pytest 2>&1 | tail -5
```

Expected: same baseline as before — 1 pre-existing kkb failure, no new ones. Test count drops by ~50 (the adapter tests we deleted).

- [ ] **Step 4:** Static greps confirm full removal:

```bash
grep -rn "llm_wrapper\|ClaudeLLMWrapper\|LLMWrapperBase" agent_core/src/ agent_core/tests/ | grep -v __pycache__
```

Expected: zero matches.

- [ ] **Step 5:** Commit:

```bash
git add -A agent_core/src/llm_wrapper agent_core/tests/test_llm_wrapper.py agent_core/tests/test_llm_wrapper_caching.py
git commit -m "$(cat <<'EOF'
refactor: delete legacy llm_wrapper/ adapter package (#292)

After PR4, nothing in agent_core consumes LLMWrapperBase /
ClaudeLLMWrapper. Removes the package and its 50+ adapter unit tests.
The neutral chat_provider/ package is now the only LLM interface.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Remove `LLMResponse` from `src/models.py`

**Files:**
- `agent_core/src/models.py`

`LLMResponse` is the legacy domain type; only the deleted adapter and its tests referenced it. After Task 3, nothing reads it.

- [ ] **Step 1:** Verify no remaining references:

```bash
grep -rn "LLMResponse" agent_core/src/ agent_core/tests/ | grep -v __pycache__ | grep -v "/docs/"
```

Expected: zero matches in source or tests. If something still references it, STOP and trace.

- [ ] **Step 2:** Delete the `LLMResponse` dataclass (around `agent_core/src/models.py:189–205`) including its leading comment block (`# LLM` section header) ONLY if no other class lives in that section. Inspect first.

- [ ] **Step 3:** Run the full suite:

```bash
cd agent_core && uv run pytest 2>&1 | tail -5
```

- [ ] **Step 4:** Commit:

```bash
git add agent_core/src/models.py
git commit -m "$(cat <<'EOF'
refactor(models): remove LLMResponse dataclass (#292)

LLMResponse was the legacy adapter's return type. With the adapter
deleted, it has zero remaining consumers. Neutral ChatResponse in
chat_provider/types.py is the canonical response type.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Update `CLAUDE.md`

**Files:**
- `CLAUDE.md` (repo root)

The CLAUDE.md mentions `agent_core/src/llm_wrapper/claude_wrapper.py` as the sole Anthropic SDK caller. Update both occurrences (they appear in different sections).

- [ ] **Step 1:** Open `CLAUDE.md` and search for `llm_wrapper`. Two locations expected:

  1. Under "Block responsibilities → Agent Core": _"All Anthropic API calls go through `agent_core/src/llm_wrapper/claude_wrapper.py`."_ → replace with: _"All LLM calls go through `agent_core/src/chat_provider/`. The package owns provider selection (Anthropic + OpenAI today; Azure/Ollama as follow-ups), neutral typing, retry/timeout, and OTel telemetry. Concrete provider files (`anthropic_provider.py`, `openai_provider.py`) are the only places that import their respective SDKs."_

  2. Under "Development guidelines": _"Agent Core is the only LLM caller. All Anthropic API interaction goes through `ClaudeLLMWrapper`."_ → replace with: _"Agent Core is the only LLM caller. All LLM interaction goes through a `ChatProviderBase` instance constructed via `build_chat_provider(agent_config)`."_

  Adjust wording to match the document's existing voice and rule-list style.

- [ ] **Step 2:** Commit:

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(claude-md): point at chat_provider/ instead of llm_wrapper (#292)

Reflects the redesign: ChatProviderBase is the single LLM call point;
the adapter package is gone.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Update `ARCHITECTURE.md`

**Files:**
- `ARCHITECTURE.md` (repo root)

Find the Agent Core block-responsibilities section. Add a short paragraph (or update the existing one) describing the chat_provider abstraction. Suggested wording:

```markdown
**LLM access** lives in `agent_core/src/chat_provider/`. `ChatProviderBase` is
the single interface every Agent Core component depends on. Concrete providers
(`AnthropicChatProvider`, `OpenAIChatProvider`) are selected via
`build_chat_provider(agent_config)` based on `agent.provider`. Each provider
owns the wire-format translation, retry/timeout, and OTel telemetry for its
SDK; nothing else in agent_core imports the underlying provider library.
Multimodal *input* (image blocks) is supported day one; image generation,
TTS, ASR, and realtime APIs are deliberately out of scope and would land as
sibling abstractions rather than as additions to ChatProviderBase.
```

(Match the document's structure — if there's a list of files per block, update that list too. Inspect before editing.)

- [ ] **Step 1:** Edit `ARCHITECTURE.md`.
- [ ] **Step 2:** Commit:

```bash
git add ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
docs(architecture): document chat_provider/ abstraction (#292)

Adds a short paragraph to the Agent Core section describing the
ChatProviderBase interface, factory, and the multi-provider boundary.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update `agent_core/README.md`

**Files:**
- `agent_core/README.md`

The README documents the legacy `ClaudeLLMWrapper` in three places (the runtime sequence diagram, the file map, and a "Currently implemented wrappers" caveat). Each needs to point at `chat_provider/` now.

- [ ] **Step 1:** Edit the runtime-sequence section (the table that lists step 8 as "ClaudeLLMWrapper — call() (sync) or stream_call() (streaming)"). Replace with: "ChatProviderBase — call() (sync) or stream() (streaming), via the configured provider (anthropic or openai)."

- [ ] **Step 2:** Edit the file-map section. Replace the `llm_wrapper/claude_wrapper.py — ClaudeLLMWrapper` entry with the chat_provider package layout:

  ```
  chat_provider/
    base.py                — ChatProviderBase ABC, Capabilities, error types, _validate_request
    types.py               — neutral Pydantic types (Message, ChatRequest, ChatResponse, …)
    anthropic_provider.py  — AnthropicChatProvider (only file in agent_core that imports `anthropic`)
    openai_provider.py     — OpenAIChatProvider (only file that imports `openai`)
    metrics.py             — OTel instruments shared by both providers
  ```

- [ ] **Step 3:** Replace the "Only the Anthropic (Claude) LLM wrapper is implemented" caveat with: "Anthropic (Claude) and OpenAI Chat Completions providers are implemented. AzureOpenAI and Ollama are planned follow-ups; they slot into `chat_provider/` without changing the orchestration layer."

- [ ] **Step 4:** Commit:

```bash
git add agent_core/README.md
git commit -m "$(cat <<'EOF'
docs(agent-core): rewrite README LLM sections for chat_provider (#292)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Add `agent.provider: anthropic` to shipped domain configs

**Files (one commit per file or one combined commit — your call):**
- `dev-kit/configs/kkb/agent_core.yaml`
- `dev-kit/configs/docs-assistant/agent_core.yaml`
- `dev-kit/configs/obsrv-docs-assistant/agent_core.yaml`
- `dev-kit/configs/the-blue-dots-economy/agent_core.yaml`

In each file, find the top-level `agent:` block. Add `provider: anthropic` near the top of that block (right after the existing `primary_model:` line if it's there, otherwise at the top). Add a brief comment matching `dev-kit/dpg/agent_core.yaml`'s style:

```yaml
agent:
  primary_model: claude-sonnet-4-5-20250514
  fallback_model: claude-haiku-4-5-20251001

  # Provider selection (#287). Defaults to anthropic on the schema; making
  # the choice explicit here so deployments are auditable.
  provider: anthropic

  # ... existing keys ...
```

The schema already defaults `provider` to `"anthropic"`, so adding it explicitly is purely documentation. Don't touch any other field.

- [ ] **Step 1:** Edit each domain config.
- [ ] **Step 2:** Smoke-test schema validation by running the kkb voice/schema test (the only test that loads merged YAML at runtime):

```bash
cd agent_core && uv run pytest tests/test_voice_length_cap.py -v
```

Expected: same baseline (the kkb failure is pre-existing — unrelated). No NEW failures.

- [ ] **Step 3:** Commit (single commit covering all four files is fine):

```bash
git add dev-kit/configs/
git commit -m "$(cat <<'EOF'
config(domains): pin agent.provider=anthropic in shipped configs (#292)

The schema defaults to anthropic, so this is documentary — making the
provider choice explicit in each deployment config so the choice is
visible and auditable. Switching a deployment to openai is now a
one-line yaml edit (plus the corresponding API key in env).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Final regression, push, draft PR

I'll run this myself.

- [ ] **Step 1:** Full agent_core suite:

```bash
cd agent_core && uv run pytest 2>&1 | tail -5
```

Expected: `~790 passed, 1 failed (pre-existing kkb)` — about 50 fewer than PR4 because we deleted the adapter tests.

- [ ] **Step 2:** Coverage:

```bash
cd agent_core && uv run pytest --cov=src --cov-report=term-missing 2>&1 | tail -10
```

Expected: ≥70% on the `src/` package.

- [ ] **Step 3:** Final leakage greps:

```bash
grep -rn "llm_wrapper\|ClaudeLLMWrapper\|LLMWrapperBase\|LLMResponse" agent_core/src/ agent_core/tests/ | grep -v __pycache__
```

Expected: zero matches.

- [ ] **Step 4:** Push and open the draft PR.

```bash
git push -u origin pr5/cleanup-and-docs
gh pr create --base feature/llm-provider-redesign --head pr5/cleanup-and-docs --draft \
  --title "PR5: delete llm_wrapper/, remove LLMResponse, pin domain configs, refresh docs" \
  --body "..."
```

---

## After PR5 merges

The integration branch `feature/llm-provider-redesign` is ready to merge to `main`. Open the final PR:

```bash
gh pr create --base main --head feature/llm-provider-redesign \
  --title "Multi-provider LLM redesign (Anthropic + OpenAI)" \
  --body "Closes #287. Bundles PR1–PR5."
```

That's the project-level merge.

---

## Self-review checklist

**Spec coverage:**

| Spec section | Plan task |
|---|---|
| §9 PR5 — delete `llm_wrapper/` package | Task 3 |
| §9 PR5 — drop `LLMResponse` | Task 4 |
| §9 PR5 — `agent.provider: anthropic` in shipped domain configs | Task 8 |
| §9 PR5 — update CLAUDE.md, ARCHITECTURE.md | Tasks 5, 6, 7 |

**Type consistency:** N/A — this PR removes types rather than adding them.

**Scope:** PR5 only. Cross-provider routing, follow-up provider tickets, and any feature work are out of scope.
