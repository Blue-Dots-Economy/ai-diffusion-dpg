# AI Diffusion DPG Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** give an adopting engineer a current architecture, a configuration guide and a verified local setup guide on the bluedots-docs site, plus a short README and a contributor map in the repo.

**Architecture:**
- Two repos, two PRs.
  - ai-diffusion-dpg (`docs/ai-diffusion-architecture`, into `deploy/voicera-vm`): a small Action Gateway change so the Signals URLs come from env, compose fixes for running against a local Signals, a live verification run, then README, ARCHITECTURE and docker README.
  - bluedots-docs (a new branch off `origin/main`): three new pages and their sidebar entries.
- The site pages are written from the verified run and from the code. They are not written from the Google docs.

**Tech Stack:**
- Python 3.12 with uv and pytest (Action Gateway).
- Docker Compose.
- Astro 7 with Starlight 0.41 (bluedots-docs; `pnpm`). Mermaid is rendered client-side from `<pre class="mermaid">` blocks.

**Spec:** `docs/superpowers/specs/2026-10-03-ai-diffusion-docs-design.md` (`e18a76f`)

## Global Constraints

- **Reader.** The reader is an engineer at an adopting organisation. Do not use internal labels anywhere in user-facing docs: no Spec A–E, D1–D3, M0–M3, F-numbers, people, or "voice-bench found…".
- **Source precedence.** Code on `deploy/voicera-vm` comes first, then `docs/superpowers/specs/`, then the Technical Specification, then "Draft - AI Agentic DPGs". Use the code's names: "Observability Layer", and `knowledge_retrieval` (a tool).
- **Block, port and responsibility table.** Use it verbatim on the site page and in the README:
  - Agent Core 8000: runs each turn; the only caller of the LLM and of the other blocks.
  - Knowledge Engine 8001: retrieval over use-case documents and glossaries, called as a tool.
  - Memory Layer 8002: session state, profile graph, saved tool results, audit.
  - Trust Layer 8003: input and output checks, consent, constraints, human handoff; fails closed.
  - Observability Layer 8004: traces, metrics, turn and outcome events (async).
  - Reach Layer 8005–8008: channels (web, voice/telephony, MCP, VoicERA bridge).
  - Action Gateway 9999: calls external systems through declared tools.
  - dev-kit 8080: a configuration tool, not a runtime block.
- **Diagrams.**
  - Exactly three: (1) who calls whom, (2) one turn, (3) config layers.
  - Write them in Mermaid. On the site use `<pre class="mermaid">…</pre>`, never a ```` ```mermaid ```` fence. In the repo use ```` ```mermaid ```` fences.
  - No images.
- **Signals URL env vars.** Use these exact names:
  - `SIGNALS_BASE_URL`, default `https://signals.bluedotseconomy.org`;
  - `SIGNALS_SEARCH_URL`, default `https://signals.bluedotseconomy.org/signals-search`;
  - `SIGNALS_INSTANCE_URL`, default `https://signals.bluedotseconomy.org`.
- **Repo git.**
  - ai-diffusion-dpg work happens in the worktree `/Users/aniket/Documents/github/aniketsaki/ai-diffusion-docs`, branch `docs/ai-diffusion-architecture`.
  - Never push, open a PR, `git stash`, `git reset --hard`, `git clean` or `git add -f`, and never commit `.superpowers/`.
  - Every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Site git.** bluedots-docs work happens in a NEW worktree `/Users/aniket/Documents/github/aniketsaki/bluedots-docs-ai` on branch `docs/ai-diffusion-dpg`, cut from `origin/main`. Never touch the existing checkout at `/Users/aniket/Documents/github/aniketsaki/blue-dots-economy/bluedots-docs` (it is on someone's `docs/installation-local-cloud-split`).
- **Secrets.** Never print `.env`, `.env.local` or any key.
- **Test phone numbers.** Use only the reserved `9199000…` range.

## Review Focus

1. **A placeholder with no env var and no default.** `${SIGNALS_BASE_URL}` has no `:-default`. If the var is unset, Action Gateway must not start with a literal `${…}` as a URL. It should fail at startup with a clear message. Pinned in Task 1, test `test_unresolved_placeholder_in_url_fails_startup`.
2. **Existing deployments that set none of the new vars must behave exactly as today.** The expanded config must equal today's URLs. Pinned in Task 1, test `test_blue_dots_urls_default_to_uat`.
3. **A `$` that is not a placeholder.** Today's configs contain `{instance_url}` (single braces) and JSONPath `$.items[…]`. Expansion must leave both untouched. Pinned in Task 1, test `test_expansion_leaves_non_placeholders`.
4. **A guide reader with the Signals stack already on 8080 and 3100.** The documented override must start without "port is already allocated". Pinned in Task 4, Step 4: run with the Signals stack up.
5. **A reader who skips `TOOL_RESULT_KEY_SECRET`.** The guide must say what breaks, which is that a job pick like "the first one" never resolves. The verification in Task 4 shows this; the guide's Common problems table carries it (Task 10).

---

## File Structure

**ai-diffusion-dpg** (worktree `ai-diffusion-docs`):
- Create `action_gateway/src/config/env_expand.py`. It holds `expand_env_vars(obj)`, plus `UnresolvedEnvPlaceholderError` and `check_no_unresolved_urls(config)`.
- Modify `action_gateway/main.py`. `_load_config` expands values, and `_build_config` calls the URL check.
- Create `action_gateway/tests/test_env_expand.py`.
- Modify `dev-kit/configs/blue-dots/action_gateway.yaml`: four `base_url`s and one `instance_url`.
- Create `agent_core/tests/test_blue_dots_signals_urls.py`, a config test that loads the Blue Dots YAML directly.
- Modify `automation/docker/docker-compose.dev.yml`:
  - Action Gateway env pass-through;
  - memory_layer `TOOL_RESULT_KEY_SECRET`;
  - pin memgraph;
  - the knowledge_engine data path.
- Create `automation/docker/local-signals.override.yml` for running next to the site's local Signals stack.
- Modify `automation/deploy/shared-vm/env.example` and `automation/deploy/shared-vm/docker-compose*.yml`: the three URL vars.
- Modify `agent_core/eval/voice_bench/stack.py`, and maybe delete `agent_core/eval/voice_bench/patches/blue-dots-local.yaml` (Task 3).
- Rewrite `README.md`.
- Rewrite `ARCHITECTURE.md`.
- Create `scripts/check_doc_paths.py`, which checks that every backticked repo path in a markdown file exists.
- Modify `automation/docker/README.md`.

**bluedots-docs** (worktree `bluedots-docs-ai`):
- Create `src/content/docs/core-concepts/architecture/ai-diffusion-dpg.md`.
- Create `src/content/docs/core-concepts/architecture/ai-diffusion-configuration.md`.
- Create `src/content/docs/guides/installation/local-setup/ai-diffusion-dpg.md`.
- Modify `astro.config.mjs`: sidebar entries.
- Modify `src/content/docs/start/build.md*`: one link to the AI Diffusion pages.

**Ruling recorded here, a deviation from spec §5.3.** The "Path N of 10" prev/next labels belong to the Signals *Build & integrate* reading path. The AI Diffusion pages are not steps on that path, so they get no `prev`/`next` labels and nothing is renumbered. Instead, `start/build` gets one "Building a conversational agent?" link.

---

### Task 1: Env-overridable Signals URLs in Action Gateway

**Files:**
- Create: `action_gateway/src/config/env_expand.py`
- Modify: `action_gateway/main.py:57-63` (`_load_config`), `action_gateway/main.py:114-124` (`_build_config`)
- Modify: `dev-kit/configs/blue-dots/action_gateway.yaml:63,157,255,482,532`
- Test: `action_gateway/tests/test_env_expand.py`, `agent_core/tests/test_blue_dots_signals_urls.py`

**Interfaces:**
- Produces:
  - `expand_env_vars(obj: Any) -> Any`;
  - `check_no_unresolved_urls(config: dict) -> None`, which raises `UnresolvedEnvPlaceholderError(ValueError)`;
  - the three env var names from the Global Constraints.

- [ ] **Step 1: Write the failing tests.** Create `action_gateway/tests/test_env_expand.py`:

```python
"""${VAR} / ${VAR:-default} expansion for Action Gateway config (same contract as reach_layer)."""
import pytest

from src.config.env_expand import (UnresolvedEnvPlaceholderError, check_no_unresolved_urls,
                                   expand_env_vars)


def test_set_var_wins(monkeypatch):
    monkeypatch.setenv("SIGNALS_BASE_URL", "http://host.docker.internal:2742")
    assert expand_env_vars({"base_url": "${SIGNALS_BASE_URL:-https://x}"}) == {
        "base_url": "http://host.docker.internal:2742"}


def test_unset_var_uses_default(monkeypatch):
    monkeypatch.delenv("SIGNALS_BASE_URL", raising=False)
    assert expand_env_vars("${SIGNALS_BASE_URL:-https://signals.bluedotseconomy.org}") == \
        "https://signals.bluedotseconomy.org"


def test_unset_var_without_default_is_left_as_written(monkeypatch):
    monkeypatch.delenv("NOPE_X", raising=False)
    assert expand_env_vars("${NOPE_X}") == "${NOPE_X}"


def test_nested_lists_and_dicts(monkeypatch):
    monkeypatch.setenv("A_X", "1")
    assert expand_env_vars({"l": [{"v": "${A_X}"}, 3, None]}) == {"l": [{"v": "1"}, 3, None]}


def test_expansion_leaves_non_placeholders():
    raw = {"body": {"item_instance_url": "{instance_url}"}, "response_path": "$.items[0].item_id",
           "price": "$5"}
    assert expand_env_vars(raw) == raw


def test_unresolved_placeholder_in_url_fails_startup(monkeypatch):
    monkeypatch.delenv("NOPE_X", raising=False)
    cfg = {"tools": [{"id": "t1", "base_url": "${NOPE_X}/api"}]}
    with pytest.raises(UnresolvedEnvPlaceholderError, match="t1.*NOPE_X"):
        check_no_unresolved_urls(cfg)


def test_unresolved_placeholder_in_static_param_fails_startup(monkeypatch):
    monkeypatch.delenv("NOPE_X", raising=False)
    cfg = {"tools": [{"id": "apply_job", "base_url": "https://a",
                      "params": [{"name": "instance_url", "source": "static", "value": "${NOPE_X}"}]}]}
    with pytest.raises(UnresolvedEnvPlaceholderError, match="apply_job.*NOPE_X"):
        check_no_unresolved_urls(cfg)


def test_resolved_config_passes():
    check_no_unresolved_urls({"tools": [{"id": "t", "base_url": "https://a",
                                         "params": [{"name": "x", "source": "static", "value": "v"}]}]})
```

Before writing, read `action_gateway/tests/conftest.py` and one existing test, such as `test_schema_config.py`, to confirm the import root. If tests import `action_gateway.src…` rather than `src…`, match that.

- [ ] **Step 2: Run the tests and watch them fail.**
  Run: `cd action_gateway && uv run --extra dev pytest tests/test_env_expand.py -q`
  Expected: FAIL with `ModuleNotFoundError: src.config.env_expand`.

- [ ] **Step 3: Implement `action_gateway/src/config/env_expand.py`.**

```python
"""
action_gateway/src/config/env_expand.py

${VAR} / ${VAR:-default} expansion for Action Gateway YAML, the same contract
as reach_layer/base/config_loader.py: a set variable wins, an unset one takes
its default, and an unset one without a default is left as written. Only
string scalars are touched. Belongs to the Action Gateway DPG block.
"""
from __future__ import annotations

import os
import re
from typing import Any

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::-(.*?))?\}")


class UnresolvedEnvPlaceholderError(ValueError):
    """A tool URL still holds a ${VAR} placeholder after expansion."""


def expand_env_vars(obj: Any) -> Any:
    """Recursively expand ``${VAR}`` and ``${VAR:-default}`` in string scalars.

    Args:
        obj: Parsed YAML (dict, list, scalar).

    Returns:
        The same structure with placeholders expanded.
    """
    if isinstance(obj, str):
        def _replace(m: re.Match) -> str:
            value = os.environ.get(m.group(1))
            if value is not None:
                return value
            return m.group(2) if m.group(2) is not None else m.group(0)
        return _ENV_VAR_PATTERN.sub(_replace, obj)
    if isinstance(obj, dict):
        return {k: expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env_vars(i) for i in obj]
    return obj


def check_no_unresolved_urls(config: dict) -> None:
    """Fail startup when a tool's base_url or static param still holds ``${VAR}``.

    Args:
        config: Merged, expanded Action Gateway config.

    Raises:
        UnresolvedEnvPlaceholderError: Naming the tool and the variable.
    """
    for tool in config.get("tools") or []:
        values = [tool.get("base_url") or ""]
        values += [p.get("value") for p in tool.get("params") or []
                   if p.get("source") == "static" and isinstance(p.get("value"), str)]
        for value in values:
            m = _ENV_VAR_PATTERN.search(value)
            if m:
                raise UnresolvedEnvPlaceholderError(
                    f"tool '{tool.get('id')}' has an unresolved ${{{m.group(1)}}}: set {m.group(1)} "
                    f"or give the placeholder a default (${{{m.group(1)}:-...}})")
```

- [ ] **Step 4: Run the tests and watch them pass.**
  Run: `cd action_gateway && uv run --extra dev pytest tests/test_env_expand.py -q`
  Expected: 8 passed.

- [ ] **Step 5: Wire the helpers into `main.py`.**
  - In `_load_config`, return `expand_env_vars(yaml.safe_load(f) or {})`.
  - In `_build_config`, after `config = _deep_merge(...)` and before `MergedConfig.validate_full(config)`, call `check_no_unresolved_urls(config)`.
  - Import both from `src.config.env_expand`. Match the import style already used in `main.py` for `src.*`.
  - Check where tools sit in the merged config. If they're not under top-level `tools`, adapt `check_no_unresolved_urls` (grep `tools` in `action_gateway/src/schema/config.py`), and update the tests' config shape to match.
  - `action_gateway/tests/test_main.py` holds inline copies of `_load_config`. Leave it as it is, and add no new copies there.

- [ ] **Step 6: Write the failing Blue Dots config test.** Create `agent_core/tests/test_blue_dots_signals_urls.py`:

```python
"""Blue Dots Action Gateway URLs come from SIGNALS_* env vars with today's UAT values as defaults."""
import re
from pathlib import Path

import yaml

AG = Path(__file__).resolve().parents[2] / "dev-kit/configs/blue-dots/action_gateway.yaml"
VARS = {"SIGNALS_BASE_URL": "https://signals.bluedotseconomy.org",
        "SIGNALS_SEARCH_URL": "https://signals.bluedotseconomy.org/signals-search",
        "SIGNALS_INSTANCE_URL": "https://signals.bluedotseconomy.org"}
PH = re.compile(r"^\$\{(\w+):-([^}]*)\}$")


def _urls():
    cfg = yaml.safe_load(AG.read_text(encoding="utf-8"))
    for tool in cfg["tools"]:
        if tool.get("base_url"):
            yield tool["id"], "base_url", tool["base_url"]
        for p in tool.get("params") or []:
            if p.get("name") == "instance_url" and p.get("source") == "static":
                yield tool["id"], "instance_url", p["value"]


def test_every_signals_url_uses_a_signals_var():
    seen = list(_urls())
    assert seen, "no URLs found"
    for tool, field, value in seen:
        m = PH.match(value)
        assert m and m.group(1) in VARS, f"{tool}.{field} = {value!r}"


def test_blue_dots_urls_default_to_uat():
    for tool, field, value in _urls():
        var, default = PH.match(value).groups()
        assert default == VARS[var], f"{tool}.{field} default {default!r} != {VARS[var]!r}"
```

Check `cfg["tools"]` against the real file before running. If tools are nested elsewhere, adjust `_urls()`.

- [ ] **Step 7: Run the test and watch it fail.**
  Run: `cd agent_core && uv run --extra dev pytest tests/test_blue_dots_signals_urls.py -q`
  Expected: FAIL on `test_every_signals_url_uses_a_signals_var` (plain URLs today).

- [ ] **Step 8: Edit `dev-kit/configs/blue-dots/action_gateway.yaml`.**
  - Each `base_url: "https://signals.bluedotseconomy.org"` (lines 63, 255, 482) becomes `base_url: "${SIGNALS_BASE_URL:-https://signals.bluedotseconomy.org}"`.
  - Line 157 becomes `base_url: "${SIGNALS_SEARCH_URL:-https://signals.bluedotseconomy.org/signals-search}"`.
  - The static `instance_url` value (line 532) becomes `value: "${SIGNALS_INSTANCE_URL:-https://signals.bluedotseconomy.org}"`.
  - Rewrite the "WHICH CLUSTER" header comment (lines 13–20) to say: set these three env vars to switch clusters, and the defaults are UAT.

- [ ] **Step 9: Run the tests and watch them pass.** Then run the dev-kit and Action Gateway suites.
  - Run: `cd agent_core && uv run --extra dev pytest tests/test_blue_dots_signals_urls.py -q`. Expected: 2 passed.
  - Run: `cd action_gateway && uv run --extra dev pytest -q`. Expected: all pass.
  - Run: `cd dev-kit && uv run --extra dev pytest -q`. Expected: only the known `test_dpg_yaml_validates[reach_layer]` failure.
  - If a dev-kit validator now rejects `${…}` in `base_url`, find it with `grep -rn base_url dev-kit/dev_kit`. Then accept the placeholder form there, and add a dev-kit test that a `${VAR:-https://…}` base_url validates. That keeps runtime and dev-kit in sync (`.claude/rules/runtime-devkit-sync.md`).

- [ ] **Step 10: Commit.**

```bash
git add action_gateway/src/config/env_expand.py action_gateway/main.py action_gateway/tests/test_env_expand.py \
        agent_core/tests/test_blue_dots_signals_urls.py dev-kit/configs/blue-dots/action_gateway.yaml
git commit -m "feat(action_gateway): expand \${VAR:-default} in config; Blue Dots Signals URLs from SIGNALS_* env

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Compose for running next to a local Signals

**Files:**
- Modify: `automation/docker/docker-compose.dev.yml`. Change the Action Gateway `environment` (around line 52), the memory_layer `environment`, the memgraph image and command (lines 621–628), and the knowledge_engine data volume (the hyphenated `../../knowledge-engine/data`).
- Create: `automation/docker/local-signals.override.yml`
- Modify: `automation/deploy/shared-vm/env.example`, and the Action Gateway environment in `automation/deploy/shared-vm/docker-compose*.yml`.

**Interfaces:**
- Consumes: the env var names from Task 1.
- Produces: the override file `automation/docker/local-signals.override.yml`, with dev-kit host port 8081, Loki host port 3101, and `host.docker.internal` on Action Gateway. Task 4 uses it.

- [ ] **Step 1: Action Gateway env pass-through** in the dev compose. Replace the two comment lines under `environment:` with the following, keeping `CONFIG_FOLDER`:

```yaml
      - CONFIG_FOLDER=/app/config
      # Connector secrets and the Signals cluster. Empty values are fine for a domain that doesn't use them.
      - BLUE_DOTS_API_KEY=${BLUE_DOTS_API_KEY:-}
      - BLUE_DOTS_ORG_ID=${BLUE_DOTS_ORG_ID:-}
      - BLUE_DOTS_SEARCH_API_KEY=${BLUE_DOTS_SEARCH_API_KEY:-}
      - SIGNALS_BASE_URL=${SIGNALS_BASE_URL:-https://signals.bluedotseconomy.org}
      - SIGNALS_SEARCH_URL=${SIGNALS_SEARCH_URL:-https://signals.bluedotseconomy.org/signals-search}
      - SIGNALS_INSTANCE_URL=${SIGNALS_INSTANCE_URL:-https://signals.bluedotseconomy.org}
```

- [ ] **Step 2: memory_layer.** Add `- TOOL_RESULT_KEY_SECRET=${TOOL_RESULT_KEY_SECRET:-}` to its `environment`.
- [ ] **Step 3: Pin memgraph and the knowledge_engine path.**
  - Set `image: memgraph/memgraph:3.13.1`. The command should be flags only: `["--storage-wal-enabled=true", "--storage-snapshot-interval-sec=300"]`. This matches the fix on `main` (`git show origin/main:automation/docker/docker-compose.dev.yml | grep -n -A6 "^  memgraph:"`); copy main's exact lines.
  - Change the knowledge_engine data mount from `../../knowledge-engine/data` to `../../knowledge_engine/data`, as on main.
- [ ] **Step 4: Create `automation/docker/local-signals.override.yml`.**

```yaml
# Run the AI Diffusion stack next to the Blue Dots local Signals stack
# (bluedots-docs: Guides → Installation → Local Setup → Local Stack). That stack
# already publishes 8080 (Keycloak) and 3100 (signals-search), so move ours.
#   docker compose -f docker-compose.dev.yml -f local-signals.override.yml up -d ...
services:
  dev_kit:
    ports: !override
      - "8081:8080"
  loki:
    ports: !override
      - "3101:3100"
  action_gateway:
    extra_hosts: ["host.docker.internal:host-gateway"]
```

Check that `!override` works with the installed Compose (`docker compose version`; v2.24 or later). If it doesn't, use the documented alternative: a separate `ports` list after removing the base mapping. Record which one was used.

- [ ] **Step 5: shared-vm.** Add the three `SIGNALS_*` vars to `automation/deploy/shared-vm/env.example` with their defaults and a one-line comment. Pass them into Action Gateway in the shared-vm compose, the same way as Step 1.
- [ ] **Step 6: Validate the compose files.**
  - Run: `cd automation/docker && docker compose -f docker-compose.dev.yml -f local-signals.override.yml config --quiet && echo ok`. Expected: `ok`.
  - Run: `docker compose -f docker-compose.dev.yml -f local-signals.override.yml config | grep -E '"?8081|3101|host.docker.internal|memgraph:3.13.1'`. Expected: all four appear.
  - Do the same `config --quiet` check for the shared-vm compose, with its env.example copied to a temp `.env`.
- [ ] **Step 7: Commit.**

```bash
git add automation/docker/docker-compose.dev.yml automation/docker/local-signals.override.yml \
        automation/deploy/shared-vm/env.example automation/deploy/shared-vm/docker-compose*.yml
git commit -m "fix(compose): pass Blue Dots and Signals env to action_gateway, tool-result secret, pin memgraph; local-Signals override

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: voice-bench uses the env vars instead of its patch file

**Files:**
- Modify: `agent_core/eval/voice_bench/stack.py`. Remove the patch application in `up()`, around lines 166–171, and set the env in `compose_override()`, around line 58.
- Delete: `agent_core/eval/voice_bench/patches/blue-dots-local.yaml`, but only if nothing else reads it (`grep -rn blue-dots-local agent_core`).
- Test: `agent_core/tests/eval/voice_bench/test_stack.py`

**Interfaces:**
- Consumes: the Task 1 env names.
- Produces: `compose_override(...)` output that includes `SIGNALS_BASE_URL={tap_url}`, `SIGNALS_SEARCH_URL={tap_url}/signals-search` and `SIGNALS_INSTANCE_URL={instance_url}` under `action_gateway.environment`.

Constraint: voice-bench builds old milestone refs (M0–M3) that don't have Task 1. For refs without `action_gateway/src/config/env_expand.py`, it must still apply the patch file. So the change is: set the env always, and apply the patch only when `env_expand.py` is absent from the target worktree. The patch file is then kept. If that makes `up()` noticeably more complex, stop and report DONE_WITH_CONCERNS; leaving voice-bench unchanged is acceptable under spec §6.

- [ ] **Step 1: Write the failing test** in `test_stack.py`. It follows the style of the existing `compose_override` tests (read them first). It asserts that the override text contains `SIGNALS_BASE_URL=` with the tap URL. A second test asserts that when the worktree has `action_gateway/src/config/env_expand.py`, `up()` does not rewrite `action_gateway.yaml`. Use the existing `FakeRun` / tmp worktree fixtures.
- [ ] **Step 2: Run the tests and watch them fail.** Run: `cd agent_core && uv run --extra dev pytest tests/eval/voice_bench/test_stack.py -q`.
- [ ] **Step 3: Implement.** Add the three vars to the `action_gateway` service's environment in `compose_override`. Then gate the patch on `not (wt / "action_gateway/src/config/env_expand.py").exists()`.
- [ ] **Step 4: Run the tests and watch them pass.** Run the whole voice-bench test dir: `uv run --extra dev pytest tests/eval/voice_bench -q`. Expected: all pass (176 or more).
- [ ] **Step 5: Commit.**

```bash
git add agent_core/eval/voice_bench/stack.py agent_core/tests/eval/voice_bench/test_stack.py
git commit -m "refactor(voice-bench): point the stack at local Signals via SIGNALS_* env; keep the patch for older refs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Live verification run (the source of truth for the setup guide)

This task writes no product docs. It produces a run log that Tasks 5, 6 and 10 are written from.

**Files:**
- Create (git-ignored): `.superpowers/docs-run/RUNLOG.md`, holding every command, its trimmed output, timings and `docker stats`.

**Interfaces:**
- Consumes: Task 1's env vars and Task 2's override file.
- Produces: `RUNLOG.md` with these sections: `## Signals stack`, `## Service account`, `## Seed job`, `## AI Diffusion up`, `## Health`, `## Web chat turn`, `## Bridge conversation`, `## Application in Signals`, `## Memory`, `## Problems hit`.

**Pre-condition (STOP and ask):** this machine already runs a Signals stack (`signals-postgres`, `signals-api`, and others; it holds the voice-bench backend). The run needs the site's Local Stack, fresh. Before touching it, ask the controller whether to stop the existing containers. Stopping them without `-v` keeps their data. Don't run `down -v` on it.

- [ ] **Step 1: Bring up the site's Local Stack, following the page exactly.** Use a fresh clone of Signals-DPG in `$CLAUDE_JOB_DIR/tmp/run/` and the commands on `bluedots-docs/src/content/docs/guides/installation/local-setup/local-stack.md` (read it from `origin/main` with `git show`), with `--profile search`. Record each command and the `/health` checks for Signals on :2742 and search on :3100.
- [ ] **Step 2: Mint the agent's service account.** In the Signals stack, run `docker compose run --rm signals-bootstrap sh -lc "pnpm --filter api db:seed:services"`, the same command voice-bench's `backend.py` `_service_identity` uses. Take the `aggregator-dpg` block's `org_id` and `apikey`. Record the command, and redact the key in the log. Note that the key is printed only once.
- [ ] **Step 3: Seed one job.** Create one provider job posting that matches the caller in Step 7 (Electrician, Lucknow), using the API that `agent_core/eval/voice_bench/seed.py` uses. Record the request with its body, the job's `item_id` and the `instance_url` from `SELECT item_instance_url FROM items WHERE item_domain='provider' LIMIT 1;`.
- [ ] **Step 4: Start AI Diffusion from a fresh clone.**
  - Clone ai-diffusion-dpg into `$CLAUDE_JOB_DIR/tmp/run/aid`. Check out THIS branch's head commit, so the Task 1–2 changes are present. In the guide this becomes `deploy/voicera-vm` once PR 1 merges.
  - Write `automation/docker/.env` with: `OPENAI_API_KEY`, `BLUE_DOTS_API_KEY`, `BLUE_DOTS_SEARCH_API_KEY` (same key), `BLUE_DOTS_ORG_ID`, `SIGNALS_BASE_URL=http://host.docker.internal:2742`, `SIGNALS_SEARCH_URL=http://host.docker.internal:3100`, `SIGNALS_INSTANCE_URL=<instance_url from Step 3>`, `TOOL_RESULT_KEY_SECRET=$(openssl rand -hex 32)`, and `DOMAIN=blue-dots`.
  - Image tag: published images may predate this branch, so build from source with `--build`, and record the build time.
  - Start the core service list without voice, ngrok or the monitoring profile: `docker compose -f docker-compose.dev.yml -f local-signals.override.yml up -d --build <list>`. The Signals stack must be up while you do this (Review Focus 4). Expected: no "port is already allocated".
- [ ] **Step 5: Health.** Hit `/health` on 8000–8004, 8005, 8008 (the bridge isn't host-published: use `docker exec … python3 -c` as `docs/local-setup.md` on `main` does) and 9999. Record each status.
- [ ] **Step 6: Web chat turn.** Open `http://localhost:8005` (or POST its chat endpoint), send "नमस्ते", and record the reply.
- [ ] **Step 7: Bridge conversation.** Use `curl -sN` against the bridge `/v1/chat/completions` with `"stream": true`, `metadata.caller_phone` set to `9199000000101` and a fixed `call_id`. Send these turns:
  1. नमस्ते
  2. हाँ ठीक है
  3. मेरी उम्र 28 साल है
  4. मुझे लखनऊ में इलेक्ट्रीशियन का काम चाहिए
  5. पहला वाला
  6. मेरा नाम राम कुमार है (if asked)
  7. हाँ, भेज दीजिए

  Record each reply. If the bridge isn't host-published, add a `ports: ["8008:8008"]` entry for `reach_layer_bridge` to the override file, back in Task 2's file, and record that change.
- [ ] **Step 8: Prove the application exists.**

  ```sql
  SELECT count(*) FROM actions WHERE created_at > now() - interval '15 minutes';
  ```

  Run that on signals-postgres, or check with a duplicate apply that returns `ACTION_LIMIT_REACHED`. Record the result. Expected: 1 application.
- [ ] **Step 9: Measure memory.** Run `docker stats --no-stream` across both stacks and record the total. This becomes the guide's RAM figure.
- [ ] **Step 10: Run the `TOOL_RESULT_KEY_SECRET` negative check** (Review Focus 5). Restart memory_layer with the secret empty, repeat turns 4–5, and record that "पहला वाला" doesn't resolve. Then restore the secret.
- [ ] **Step 11: Tear down only what this task started.** Run `docker compose … down -v` for the AI Diffusion run project, and stop the run's Signals stack. Then restart the pre-existing Signals containers if Step 0 stopped them, and record that.
- [ ] **Step 12: No commit.** `RUNLOG.md` is git-ignored. Report the path.

---

### Task 5: README.md

**Files:**
- Rewrite: `README.md` (about 80 lines).

**Interfaces:**
- Consumes: the block table from the Global Constraints, the RUNLOG quick-start commands, and the site page URLs: `https://blue-dots-economy.github.io/bluedots-docs/core-concepts/architecture/ai-diffusion-dpg/`, `…/core-concepts/architecture/ai-diffusion-configuration/` and `…/guides/installation/local-setup/ai-diffusion-dpg/`.

- [ ] **Step 1: Write the README** with these sections, in order:
  1. `# AI Diffusion DPG`, then three lines on what it is.
  2. `## The seven blocks`: the table from the Global Constraints, plus the dev-kit line.
  3. `## Quick start`. The fastest path (cloud Signals, OpenAI key) in five commands. Commands only; anything that needs explaining is a link to the site guide. Take them from RUNLOG Step 4 with the UAT defaults. State that `BLUE_DOTS_*` keys are needed, and link the guide for running against a local Signals.
  4. `## Repository map`: one line per top-level directory (`ls -d */` at HEAD; skip `logs/`).
  5. `## Documentation`: the three site pages, plus `docs/superpowers/specs/` (designs), `agent_core/eval/` (NLU, scenarios, voice-bench) and `ARCHITECTURE.md` (the contributor map).
  6. `## Contributing`:
     - branches go through PRs into `deploy/voicera-vm`;
     - a runtime schema change updates the dev-kit mirror (`.claude/rules/runtime-devkit-sync.md`);
     - each block's tests run with `cd <block> && uv run --extra dev pytest`.
  7. `## Licence`, linking `LICENSE`.

  No status tables, no KKB references, and no `run --rm reach_layer`.
- [ ] **Step 2: Check the facts.**
  - Every port appears in `automation/docker/docker-compose.dev.yml`.
  - Every path exists: `python3 scripts/check_doc_paths.py README.md`. The script is written in Task 6; if Task 6 hasn't run yet, check the paths by hand with `ls`.
  - `grep -niE "kkb|spec [a-e]|todo|pending|stub" README.md` returns nothing.
- [ ] **Step 3: Commit.**

```bash
git add README.md && git commit -m "docs(readme): rewrite for adopters — blocks, quick start, repo map, where docs live

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: ARCHITECTURE.md as a contributor map, plus a path checker

**Files:**
- Create: `scripts/check_doc_paths.py`
- Test: `agent_core/tests/test_check_doc_paths.py`. `scripts/` is new and has no test setup, so agent_core's pytest runs it.
- Rewrite: `ARCHITECTURE.md` (about 120 lines).

**Interfaces:**
- Produces: `check_doc_paths.py <md>…`. It exits 0 when every backticked token that looks like a repo path exists, and exits 1 listing the missing ones otherwise. A token looks like a repo path when it contains `/` and ends in a file extension or `/`, and isn't a URL.

- [ ] **Step 1: Write the failing test.**

```python
import subprocess, sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_doc_paths.py"  # agent_core/tests/ → repo root


def run(md_text, tmp_path):
    md = tmp_path / "x.md"; md.write_text(md_text, encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(md)], capture_output=True, text=True,
                          cwd=SCRIPT.parents[1])


def test_existing_paths_pass(tmp_path):
    assert run("See `agent_core/src/orchestrator.py` and `dev-kit/`.", tmp_path).returncode == 0


def test_missing_path_fails_and_is_named(tmp_path):
    r = run("See `agent_core/src/nope.py`.", tmp_path)
    assert r.returncode == 1 and "agent_core/src/nope.py" in r.stdout


def test_urls_and_non_paths_ignored(tmp_path):
    assert run("`https://x.org/a.md` `pnpm build` `/process_turn` `a/b`", tmp_path).returncode == 0
```

- [ ] **Step 2: Run the test and watch it fail.** There is no script yet.
- [ ] **Step 3: Implement `scripts/check_doc_paths.py`.**

```python
#!/usr/bin/env python3
"""Check that every repo path in backticks in the given markdown files exists (run from the repo root)."""
import re
import sys
from pathlib import Path

TOKEN = re.compile(r"`([^`\s]+)`")
LOOKS_LIKE_PATH = re.compile(r"^(?!https?://)(?!/)[\w.\-]+(/[\w.\-]+)*(\.[A-Za-z0-9]+|/)$")


def missing(md: Path, root: Path) -> list[str]:
    out = []
    for tok in TOKEN.findall(md.read_text(encoding="utf-8")):
        tok = tok.split(":")[0]  # allow path:line
        if "/" in tok and LOOKS_LIKE_PATH.match(tok) and not (root / tok).exists():
            out.append(tok)
    return sorted(set(out))


def main(argv: list[str]) -> int:
    root = Path.cwd()
    bad = {a: missing(Path(a), root) for a in argv}
    for a, toks in bad.items():
        for t in toks:
            print(f"{a}: missing {t}")
    return 1 if any(bad.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the test and watch it pass.**
- [ ] **Step 5: Write `ARCHITECTURE.md`** with these sections, in order:
  1. `# AI Diffusion DPG — contributor map`, then one line linking to the site architecture page for the full picture.
  2. `## Blocks in the code`: a table of block → directory → entry point (`main.py`, server module) → config schema (`<block>/src/schema/config.py`) → dev-kit mirror (`dev-kit/dev_kit/schemas/{dpg,domain}/<block>.py`). Verify each cell with `ls`.
  3. `## Following a turn in the code`: a table of `[STEP n]` log marker → function → file. Build it from `grep -n "\[STEP" agent_core/src/orchestrator.py`, and cover sync `_process_turn_inner` and stream `_stream_turn_impl`. Include bootstrap, dialogue-act NLU (`agent_core/src/understanding/`), handoff (`agent_core/src/handoff.py`), pre-dispatch (`agent_core/src/predispatch/`), the output guard (`agent_core/src/output/guard.py`) and post-turn writes.
  4. `## Rules`:
     - only Agent Core calls other blocks;
     - each block has a base class and an implementation;
     - Trust fails closed;
     - a runtime schema change updates the dev-kit mirror and FIELD_RULES (`.claude/rules/runtime-devkit-sync.md`);
     - each layer's config is `extra="forbid"`, so an unknown key stops startup.
  5. `## Configuration loading`: `dev-kit/dpg/<block>.yaml` plus `dev-kit/configs/<domain>/<block>.yaml`, mounted as `config/dpg.yaml` + `config/<block>.yaml` (compose), deep-merged and then validated. Add `${VAR:-default}` expansion in Reach Layer and Action Gateway. Include one ```` ```mermaid ```` diagram: the same as site Diagram 3, written in Task 9. Write it here first, and Task 9 copies it.
  6. `## Tests and evals`: per-block `tests/`, `agent_core/eval/{nlu,scenarios,voice_bench}`, and how to run each, with one command each.
  7. `## Adding a feature to a block`: a checklist of schema, dev-kit mirror, tests, config docs, and the site page if user-visible.

  Delete everything from the old file, including status, design changes and the stub guide.
- [ ] **Step 6: Run the checker.** `python3 scripts/check_doc_paths.py ARCHITECTURE.md README.md`. Expected: exit 0.
- [ ] **Step 7: Commit.**

```bash
git add scripts/check_doc_paths.py agent_core/tests/test_check_doc_paths.py ARCHITECTURE.md
git commit -m "docs(architecture): contributor map with turn-to-code table; path checker for repo docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: automation/docker/README.md corrections

**Files:**
- Modify: `automation/docker/README.md`

- [ ] **Step 1: Correct the stale lines.**
  - Images come from GHCR (`ghcr.io/blue-dots-economy/ai-diffusion-dpg/<block>:<tag>`), not Docker Hub.
  - Blue Dots uses `OPENAI_API_KEY`, not Anthropic.
  - Remove the CLI profile and the `run --rm reach_layer` instructions.
  - Copy the resource limits from the compose `deploy.resources.limits` values.
  - Add a short "Running against a local Signals" paragraph that points to `local-signals.override.yml` and the site guide.
  - Keep the Monitoring section.
- [ ] **Step 2: Verify.** `grep -niE "docker hub|anthropic|--profile cli|run --rm reach_layer" automation/docker/README.md` returns nothing, and `python3 scripts/check_doc_paths.py automation/docker/README.md` exits 0.
- [ ] **Step 3: Commit.**

```bash
git add automation/docker/README.md && git commit -m "docs(docker): fix stale image source, key, CLI profile and limits; link local-Signals setup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Site page: AI Diffusion DPG architecture

**Files:**
- Create the site worktree first: `cd /Users/aniket/Documents/github/aniketsaki/blue-dots-economy/bluedots-docs && git fetch origin && git worktree add -b docs/ai-diffusion-dpg /Users/aniket/Documents/github/aniketsaki/bluedots-docs-ai origin/main`, then `cd ../../bluedots-docs-ai && pnpm install`.
- Create: `src/content/docs/core-concepts/architecture/ai-diffusion-dpg.md`
- Modify: `astro.config.mjs`. Add `{ label: 'AI Diffusion DPG Architecture', slug: 'core-concepts/architecture/ai-diffusion-dpg' }` after the Aggregator entry.

- [ ] **Step 1: Frontmatter**, following the pattern of `aggregator-dpg.md`:

```yaml
---
title: AI Diffusion DPG Architecture
description: How the AI Diffusion DPG runs a conversational agent for a public-service use case — seven services, one turn, and the principles behind them.
sidebar:
  order: 4
---
```

Use the next free `order` after `aggregator-dpg.md`; read its value first.
- [ ] **Step 2: Write the sections from spec §5.1**, in order:
  1. What it is.
  2. The seven blocks. The table comes verbatim from the Global Constraints.
  3. Diagram 1.
  4. Diagram 2, the one-turn walk-through with its Blue Dots example and why each step is there.
  5. Design principles, with six paragraphs, each leading with its why.
  6. Channels.
  7. Built today vs planned. Date it 2026-10-03 and link the issue for each planned item. Find them with `gh issue list -R Blue-Dots-Economy/ai-diffusion-dpg --search "<topic>"`; if there's no issue, write it without a link.
  8. Read next.

  Diagram 1, as written into the page:

```html
<pre class="mermaid">
flowchart LR
  subgraph Channels["Reach Layer channels"]
    W[Web chat] --- V[Voice / telephony] --- M[MCP] --- B[VoicERA bridge]
  end
  Channels --> AC[Agent Core]
  AC --> TL[Trust Layer]
  AC --> ML[Memory Layer]
  AC --> KE[Knowledge Engine]
  AC --> AG[Action Gateway]
  AC --> OL[Observability Layer]
  AC --> LLM[(LLM provider)]
  AG --> EXT[(External systems, e.g. Signals)]
</pre>
```

  Diagram 2 is a `sequenceDiagram` with participants Caller, Reach, Agent Core, Trust, Memory, Action Gateway and LLM. Its messages follow the ten steps in spec §5.1 item 4, and the async writes go in an `Note over` after the reply. Before writing it, check the step order against `ARCHITECTURE.md` `## Following a turn in the code` (Task 6).
- [ ] **Step 3: Check the facts.** Every claim in "Built today" must trace to a file on `deploy/voicera-vm`. List the file for each in the task report, not on the page. Run `grep -niE "spec [a-e]|kkb|d[123]\b|m[0-3]\b|voice-bench found" src/content/docs/core-concepts/architecture/ai-diffusion-dpg.md`; it should return nothing.
- [ ] **Step 4: Build.** `pnpm build`. Expected: success, with no new warnings for this page.
- [ ] **Step 5: Commit** in the site worktree.

```bash
git add src/content/docs/core-concepts/architecture/ai-diffusion-dpg.md astro.config.mjs
git commit -m "docs(ai-diffusion): architecture page — blocks, one turn, principles, channels

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Site page: Configuring a use case

**Files:**
- Create: `src/content/docs/core-concepts/architecture/ai-diffusion-configuration.md`. Use `sidebar.order` = Task 8's order + 1, with the title "Configuring an AI Diffusion Use Case".
- Modify: `astro.config.mjs`. Add an entry after the Task 8 entry.

- [ ] **Step 1: Write the sections from spec §5.2:**
  1. Two layers, with Diagram 3. Copy the Mermaid from `ARCHITECTURE.md` and wrap it in `<pre class="mermaid">`.
  2. What you write, block by block: a table, with each row pointing to the real key in `dev-kit/configs/blue-dots/<block>.yaml`.
  3. One phase, end to end. A trimmed `job_match` excerpt from `dev-kit/configs/blue-dots/agent_core.yaml`, at most 40 lines: the description, the `tools`, the `pending` list, the `termination_intent` rule and the `apply_now` rule. Then a walk through the Diagram 2 turn showing which lines fired.
  4. What the framework owns.
  5. The dev-kit.
  6. Common mistakes. Include the image-schema mismatch at startup, and the missing `TOOL_RESULT_KEY_SECRET`.
- [ ] **Step 2: Check the excerpt.** Every YAML key in it exists in the current file: `grep -n "<key>" dev-kit/configs/blue-dots/agent_core.yaml` in the ai-diffusion worktree.
- [ ] **Step 3:** Run `pnpm build`, which must succeed.
- [ ] **Step 4: Commit.**

```bash
git add src/content/docs/core-concepts/architecture/ai-diffusion-configuration.md astro.config.mjs
git commit -m "docs(ai-diffusion): configuring a use case — layers, what you write, a phase end to end

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Site guide: AI Diffusion local setup

**Files:**
- Create: `src/content/docs/guides/installation/local-setup/ai-diffusion-dpg.md`. Use `sidebar.order` 5 (after `aggregator-dpg.md`'s 4), with the title "AI Diffusion DPG Setup".
- Modify: `astro.config.mjs`, adding the Local Setup entry. Modify `src/content/docs/start/build.md*`, adding one link line (see the File Structure ruling).

**Interfaces:**
- Consumes: `.superpowers/docs-run/RUNLOG.md` from Task 4, in the ai-diffusion worktree. Every command in the guide is copied from it. None may be invented.

- [ ] **Step 1: Write the ten steps from spec §5.3.** At the top, put a `:::note` saying "Verified on 2026-10-03 against ai-diffusion-dpg `<commit>`", using RUNLOG's commit.
  - Commands come from RUNLOG, with keys shown as `<your key>`.
  - The memory figure comes from RUNLOG `## Memory`.
  - Common problems come from RUNLOG `## Problems hit`, plus these fixed rows:
    - memgraph doesn't start → old unpinned image → pull the pinned compose;
    - Action Gateway exits with an "unresolved ${…}" message → set the var or the default;
    - a pick like "the first one" is never understood → `TOOL_RESULT_KEY_SECRET` unset (RUNLOG Step 10);
    - a service fails at startup after a config pull → the image is older than the config, so rebuild or pull a matching tag;
    - port already allocated → use `local-signals.override.yml`.
  - Use the reserved test phone `9199000000101`.
- [ ] **Step 2: Check the facts.** Every command in the guide appears in RUNLOG (diff them by hand, or with a small grep loop over fenced `bash` blocks). The ports match the override file.
- [ ] **Step 3:** Run `pnpm build`, which must succeed.
- [ ] **Step 4: Commit.**

```bash
git add src/content/docs/guides/installation/local-setup/ai-diffusion-dpg.md astro.config.mjs src/content/docs/start/build.*
git commit -m "docs(ai-diffusion): local setup guide against the local Signals stack, verified end to end

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Render check and PR preparation (no push)

- [ ] **Step 1: Check the site in a browser.**
  - Run `pnpm dev` in the site worktree, in the background.
  - Open the three pages at `http://localhost:4321/bluedots-docs/…` in a browser.
  - Confirm each of the three Mermaid diagrams renders as an SVG with no error text, in both light and dark themes.
  - Confirm the sidebar shows the three entries in place, and `start/build` links to them.
  - Save screenshots to `$CLAUDE_JOB_DIR/tmp/docs-shots/`, then stop `pnpm dev`.
- [ ] **Step 2: Check the repo diagrams.** Render the Mermaid blocks in `ARCHITECTURE.md` with `npx -y @mermaid-js/mermaid-cli -i ARCHITECTURE.md -o $CLAUDE_JOB_DIR/tmp/arch-check.md` (it renders every block). Expected: no parse error.
- [ ] **Step 3: Run the suites in the ai-diffusion worktree.** Run agent_core, action_gateway, dev-kit, memory_layer and trust_layer with `uv run --extra dev pytest -q`. Expected: only the known failures: dev-kit `test_dpg_yaml_validates[reach_layer]` and trust_layer `test_check_consent_returns_granted`.
- [ ] **Step 4: Write the PR bodies.** Write them to `$CLAUDE_JOB_DIR/tmp/pr-docs-1.md` (ai-diffusion-dpg) and `pr-docs-2.md` (bluedots-docs), using the house format: Summary / Release Notes / In Plain Terms / Checklist, ending with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. PR 1 notes the new env vars and the compose changes. PR 2 notes that it depends on PR 1.
- [ ] **Step 5: Stop.** The controller asks the user before any push or PR. After both PRs are open, file one child Task per PR under story #460 (use the `creating-bluedots-tickets` skill with `--parent 460`), with story points from the user (spec §9).
