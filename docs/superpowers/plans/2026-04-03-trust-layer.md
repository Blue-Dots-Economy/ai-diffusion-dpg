# Trust Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `BasicTrustLayer` stub with four production sub-blocks (Content, Guardrails, Consent, HiTL), add pre-LLM guardrail constraint assembly, implement a DPDP consent gate in the orchestrator, and make the entire Trust Layer fail-closed.

**Architecture:** The Trust Layer (port 8003) gains three new endpoints (`/assemble_constraints`, `/consent/verify`, `/escalate`) and four internal sub-block modules. Agent Core gains a consent gate in the orchestrator, `active_risks` in `NLUResult`, and guardrail constraint injection in `ManagerAgent`. All Trust Layer HTTP error handlers in Agent Core flip from fail-open to fail-closed.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, httpx, pytest, uv. YAML config via existing `dev-kit/loader.py`.

---

## File Map

**Trust Layer — create:**
- `trust_layer/src/models.py` — all Pydantic request/response types
- `trust_layer/src/blocks/__init__.py` — package marker
- `trust_layer/src/blocks/content.py` — ContentBlock (phrase-match I/O checks)
- `trust_layer/src/blocks/guardrails.py` — GuardrailsBlock (policy pack → constraints)
- `trust_layer/src/blocks/consent.py` — ConsentBlock (phrase eval)
- `trust_layer/src/blocks/hitl.py` — HiTLBlock (escalation queue)
- `trust_layer/src/trust_layer.py` — TrustLayer orchestrator
- `trust_layer/tests/blocks/__init__.py`
- `trust_layer/tests/blocks/test_content.py`
- `trust_layer/tests/blocks/test_guardrails.py`
- `trust_layer/tests/blocks/test_consent.py`
- `trust_layer/tests/blocks/test_hitl.py`

**Trust Layer — modify:**
- `trust_layer/src/server.py` — new endpoints, use TrustLayer orchestrator
- `trust_layer/src/guardrails.py` — stub implementations of new methods (for test compatibility)
- `trust_layer/tests/test_server.py` — add new endpoint tests
- `dev-kit/configs/kkb/trust_layer.yaml` — add policy_packs, consent, hitl sections

**Agent Core — modify:**
- `agent_core/src/models.py` — add `active_risks` to `NLUResult`
- `agent_core/src/interfaces/trust_layer.py` — add `assemble_constraints()`, `verify_consent()`, `escalate()` abstract methods
- `agent_core/src/http_clients/trust_layer.py` — implement new methods + fail-closed errors
- `agent_core/src/preprocessing/nlu_processor.py` — populate `active_risks` from LLM response
- `agent_core/src/orchestrator.py` — consent gate + `/assemble_constraints` call
- `agent_core/src/manager_agent.py` — guardrail constraint injection into system prompt
- `dev-kit/dpg/agent_core.yaml` — add `ask_for_consent: false`, `consent_prompt: ""`

---

## Task 1: Trust Layer Pydantic models

**Files:**
- Create: `trust_layer/src/models.py`
- Test: `trust_layer/tests/test_models.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# trust_layer/tests/test_models.py
from trust_layer.src.models import (
    InputCheckRequest,
    AssembleConstraintsRequest,
    GuardrailConstraints,
    ConsentVerifyRequest,
    ConsentVerifyResponse,
    HiTLEscalateRequest,
    HiTLEscalateResponse,
)


def test_input_check_request_with_risks():
    r = InputCheckRequest(session_id="s1", message="hello", active_risks=["false_certainty"])
    assert r.active_risks == ["false_certainty"]


def test_input_check_request_no_risks():
    r = InputCheckRequest(session_id="s1", message="hello")
    assert r.active_risks is None


def test_assemble_constraints_request():
    r = AssembleConstraintsRequest(
        session_id="s1",
        workflow_step="ready",
        active_risks=["false_certainty"],
        user_segment="labour",
    )
    assert r.user_segment == "labour"


def test_guardrail_constraints_defaults():
    g = GuardrailConstraints()
    assert g.prompt_constraints == []
    assert g.required_disclosures == []
    assert g.action_gates == {}
    assert g.refusal_templates == {}


def test_consent_verify_response():
    r = ConsentVerifyResponse(granted=True)
    assert r.granted is True


def test_hitl_escalate_response():
    r = HiTLEscalateResponse(queued=True, ticket_id="TKT-001", holding_message="Wait...")
    assert r.queued is True
```

- [ ] **Step 2: Run test — verify it fails**

```bash
cd trust_layer && uv run pytest tests/test_models.py -v
```
Expected: `ImportError: cannot import name 'InputCheckRequest'`

- [ ] **Step 3: Create `trust_layer/src/models.py`**

```python
"""
trust_layer/src/models.py

Pydantic request and response models for all Trust Layer endpoints.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class InputCheckRequest(BaseModel):
    """Request body for POST /check/input."""
    session_id: str
    message: str
    active_risks: Optional[list[str]] = None


class OutputCheckRequest(BaseModel):
    """Request body for POST /check/output."""
    session_id: str
    response: str


class ConsentCheckRequest(BaseModel):
    """Request body for POST /check/consent (connector-level)."""
    session_id: str
    connector_name: str


class TrustCheckResponse(BaseModel):
    """Response for /check/input and /check/output."""
    passed: bool
    action: str                      # "allow" | "block" | "escalate"
    reason: Optional[str] = None


class ConsentResponse(BaseModel):
    """Response for POST /check/consent."""
    granted: bool


class AssembleConstraintsRequest(BaseModel):
    """Request body for POST /assemble_constraints."""
    session_id: str
    workflow_step: str
    active_risks: list[str]
    user_segment: Optional[str] = None


class GuardrailConstraints(BaseModel):
    """Response for POST /assemble_constraints."""
    prompt_constraints: list[str] = []
    required_disclosures: list[str] = []
    action_gates: dict[str, bool] = {}
    refusal_templates: dict[str, str] = {}


class ConsentVerifyRequest(BaseModel):
    """Request body for POST /consent/verify."""
    session_id: str
    user_message: str


class ConsentVerifyResponse(BaseModel):
    """Response for POST /consent/verify."""
    granted: bool


class HiTLEscalateRequest(BaseModel):
    """Request body for POST /escalate."""
    session_id: str
    escalation_reason: str
    user_message: str
    workflow_step: str


class HiTLEscalateResponse(BaseModel):
    """Response for POST /escalate."""
    queued: bool
    ticket_id: str
    holding_message: str


class StatusResponse(BaseModel):
    """Response for GET /health."""
    status: str
```

- [ ] **Step 4: Run test — verify it passes**

```bash
cd trust_layer && uv run pytest tests/test_models.py -v
```
Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add trust_layer/src/models.py trust_layer/tests/test_models.py
git commit -m "feat(trust-layer): add Pydantic models for new endpoints"
```

---

## Task 2: ContentBlock

**Files:**
- Create: `trust_layer/src/blocks/__init__.py`, `trust_layer/src/blocks/content.py`
- Create: `trust_layer/tests/blocks/__init__.py`, `trust_layer/tests/blocks/test_content.py`

- [ ] **Step 1: Write the failing tests**

```python
# trust_layer/tests/blocks/test_content.py
import pytest
from trust_layer.src.blocks.content import ContentBlock


@pytest.fixture
def config():
    return {
        "trust": {
            "input_rules": {
                "blocked_phrases": ["bomb", "kill"],
                "escalation_topics": ["suicide", "police case"],
                "blocked_input_message": "Cannot help.",
            },
            "output_rules": {
                "blocked_phrases": ["guaranteed placement"],
                "output_blocked_message": "Bad output.",
            },
        }
    }


@pytest.fixture
def block(config):
    return ContentBlock(config)


# ── check_input normal ────────────────────────────────────────────────────
def test_check_input_allow(block):
    result = block.check_input("s1", "main electrician kaam chahiye")
    assert result["action"] == "allow"
    assert result["passed"] is True


def test_check_input_block(block):
    result = block.check_input("s1", "main bomb banana chahta hoon")
    assert result["action"] == "block"
    assert result["passed"] is False


def test_check_input_escalate(block):
    result = block.check_input("s1", "maine suicide ke baare mein socha")
    assert result["action"] == "escalate"
    assert result["passed"] is False


def test_check_input_case_insensitive(block):
    result = block.check_input("s1", "BOMB ka kya naam hai")
    assert result["action"] == "block"


# ── check_input edge ──────────────────────────────────────────────────────
def test_check_input_empty_message(block):
    result = block.check_input("s1", "")
    assert result["action"] == "allow"


def test_check_input_none_message(block):
    result = block.check_input("s1", None)
    assert result["action"] == "allow"


def test_check_input_active_risks_none_no_error(block):
    result = block.check_input("s1", "hello", active_risks=None)
    assert result["action"] == "allow"


def test_check_input_none_session_raises(block):
    with pytest.raises(ValueError):
        block.check_input(None, "hello")


# ── check_output normal ───────────────────────────────────────────────────
def test_check_output_allow(block):
    result = block.check_output("s1", "Yahan kuch jobs hain jo match karti hain.")
    assert result["action"] == "allow"


def test_check_output_block(block):
    result = block.check_output("s1", "Aapko guaranteed placement milegi.")
    assert result["action"] == "block"


# ── check_output edge ─────────────────────────────────────────────────────
def test_check_output_empty(block):
    result = block.check_output("s1", "")
    assert result["action"] == "allow"


# ── failure: missing config sections ─────────────────────────────────────
def test_missing_trust_config():
    block = ContentBlock({})
    result = block.check_input("s1", "bomb")
    assert result["action"] == "allow"   # no phrases loaded → allow (no false positive)
```

- [ ] **Step 2: Run — verify failure**

```bash
cd trust_layer && uv run pytest tests/blocks/test_content.py -v
```
Expected: `ModuleNotFoundError: No module named 'trust_layer.src.blocks'`

- [ ] **Step 3: Create package markers**

```bash
touch trust_layer/src/blocks/__init__.py
touch trust_layer/tests/blocks/__init__.py
```

- [ ] **Step 4: Create `trust_layer/src/blocks/content.py`**

```python
"""
trust_layer/src/blocks/content.py

ContentBlock — phrase-based input and output safety checks.

Reads blocked_phrases, escalation_topics, and blocked_phrases (output) from
trust.input_rules and trust.output_rules config sections. All matching is
case-insensitive substring search.

active_risks is accepted but not acted upon in this implementation — it is
passed through for future semantic matching upgrades.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class ContentBlock:
    """
    Phrase-match input/output content checker.

    Args:
        config: Full config dict containing a "trust" section.
    """

    def __init__(self, config: dict) -> None:
        trust_cfg = (config or {}).get("trust", {})
        input_cfg = trust_cfg.get("input_rules", {})
        output_cfg = trust_cfg.get("output_rules", {})

        self._blocked_input: list[str] = [
            p.lower() for p in input_cfg.get("blocked_phrases", []) if p
        ]
        self._escalation_topics: list[str] = [
            t.lower() for t in input_cfg.get("escalation_topics", []) if t
        ]
        self._blocked_output: list[str] = [
            p.lower() for p in output_cfg.get("blocked_phrases", []) if p
        ]

        logger.info(
            "content_block.init",
            extra={
                "operation": "content_block.init",
                "status": "success",
                "blocked_input_count": len(self._blocked_input),
                "escalation_count": len(self._escalation_topics),
                "blocked_output_count": len(self._blocked_output),
            },
        )

    def check_input(
        self,
        session_id: str,
        user_message: str | None,
        active_risks: list[str] | None = None,
    ) -> dict:
        """
        Check user input against blocked phrases and escalation topics.

        Args:
            session_id: Current session identifier.
            user_message: Raw user input.
            active_risks: Risk signals from NLU (accepted, not yet acted upon).

        Returns:
            dict with keys: passed (bool), action (str), reason (str | None).
            action is "allow", "block", or "escalate".
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()

        if not user_message:
            return _result(passed=True, action="allow")

        lower = user_message.lower()

        for phrase in self._blocked_input:
            if phrase in lower:
                logger.warning(
                    "content_block.input_blocked",
                    extra={
                        "operation": "content_block.check_input",
                        "status": "failure",
                        "session_id": session_id,
                        "reason": f"blocked_phrase:{phrase}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _result(passed=False, action="block", reason=f"blocked_phrase:{phrase}")

        for topic in self._escalation_topics:
            if topic in lower:
                logger.warning(
                    "content_block.input_escalated",
                    extra={
                        "operation": "content_block.check_input",
                        "status": "failure",
                        "session_id": session_id,
                        "reason": f"escalation_topic:{topic}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _result(passed=False, action="escalate", reason=f"escalation_topic:{topic}")

        logger.info(
            "content_block.input_allowed",
            extra={
                "operation": "content_block.check_input",
                "status": "success",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return _result(passed=True, action="allow")

    def check_output(self, session_id: str, llm_response: str | None) -> dict:
        """
        Check LLM output against blocked phrases.

        Args:
            session_id: Current session identifier.
            llm_response: LLM-generated response text.

        Returns:
            dict with keys: passed (bool), action (str), reason (str | None).
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()

        if not llm_response:
            return _result(passed=True, action="allow")

        lower = llm_response.lower()

        for phrase in self._blocked_output:
            if phrase in lower:
                logger.warning(
                    "content_block.output_blocked",
                    extra={
                        "operation": "content_block.check_output",
                        "status": "failure",
                        "session_id": session_id,
                        "reason": f"blocked_output_phrase:{phrase}",
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return _result(passed=False, action="block", reason=f"blocked_output_phrase:{phrase}")

        logger.info(
            "content_block.output_allowed",
            extra={
                "operation": "content_block.check_output",
                "status": "success",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return _result(passed=True, action="allow")


def _result(passed: bool, action: str, reason: str | None = None) -> dict:
    """Build a standard check result dict."""
    return {"passed": passed, "action": action, "reason": reason}
```

- [ ] **Step 5: Run — verify pass**

```bash
cd trust_layer && uv run pytest tests/blocks/test_content.py -v
```
Expected: all 13 tests PASS

- [ ] **Step 6: Commit**

```bash
git add trust_layer/src/blocks/__init__.py trust_layer/src/blocks/content.py \
        trust_layer/tests/blocks/__init__.py trust_layer/tests/blocks/test_content.py
git commit -m "feat(trust-layer): add ContentBlock with phrase-match input/output checks"
```

---

## Task 3: GuardrailsBlock

**Files:**
- Create: `trust_layer/src/blocks/guardrails.py`
- Create: `trust_layer/tests/blocks/test_guardrails.py`

- [ ] **Step 1: Write the failing tests**

```python
# trust_layer/tests/blocks/test_guardrails.py
import pytest
from trust_layer.src.blocks.guardrails import GuardrailsBlock


KKB_CONFIG = {
    "trust": {
        "policy_pack": "kkb_advisory_jobs",
        "policy_packs": {
            "kkb_advisory_jobs": {
                "risks": ["false_certainty", "emotional_overreach"],
                "guardrails": {
                    "false_certainty": {
                        "id": "GR-001",
                        "severity": "blocker",
                        "failure_mode": "block",
                        "prompt_constraints": [
                            "MUST NOT guarantee job outcomes",
                        ],
                        "required_disclosures": ["Hiring decisions rest with employer"],
                        "refusal_template": "Main guarantee nahi de sakta.",
                    },
                    "emotional_overreach": {
                        "id": "GR-003",
                        "severity": "warning",
                        "failure_mode": "constrain",
                        "prompt_constraints": ["MUST NOT provide counselling"],
                        "required_disclosures": [],
                        "refusal_template": None,
                    },
                },
            }
        },
    }
}


@pytest.fixture
def block():
    return GuardrailsBlock(KKB_CONFIG)


# ── normal ────────────────────────────────────────────────────────────────
def test_known_risk_returns_constraints(block):
    result = block.assemble_constraints(
        session_id="s1",
        workflow_step="ready",
        active_risks=["false_certainty"],
        user_segment=None,
    )
    assert "MUST NOT guarantee job outcomes" in result["prompt_constraints"]
    assert "Hiring decisions rest with employer" in result["required_disclosures"]
    assert result["refusal_templates"]["false_certainty"] == "Main guarantee nahi de sakta."


def test_multiple_risks_merged(block):
    result = block.assemble_constraints(
        session_id="s1",
        workflow_step="ready",
        active_risks=["false_certainty", "emotional_overreach"],
        user_segment=None,
    )
    assert len(result["prompt_constraints"]) == 2
    assert "MUST NOT provide counselling" in result["prompt_constraints"]


def test_refusal_template_none_excluded(block):
    result = block.assemble_constraints(
        session_id="s1",
        workflow_step="ready",
        active_risks=["emotional_overreach"],
        user_segment=None,
    )
    assert "emotional_overreach" not in result["refusal_templates"]


# ── edge ──────────────────────────────────────────────────────────────────
def test_empty_active_risks_returns_empty(block):
    result = block.assemble_constraints("s1", "ready", [], None)
    assert result["prompt_constraints"] == []
    assert result["required_disclosures"] == []


def test_unknown_risk_skipped(block):
    result = block.assemble_constraints("s1", "ready", ["scope_breach"], None)
    assert result["prompt_constraints"] == []


def test_none_session_raises(block):
    with pytest.raises(ValueError):
        block.assemble_constraints(None, "ready", ["false_certainty"], None)


# ── failure ───────────────────────────────────────────────────────────────
def test_missing_policy_pack_returns_empty():
    block = GuardrailsBlock({})
    result = block.assemble_constraints("s1", "ready", ["false_certainty"], None)
    assert result["prompt_constraints"] == []
```

- [ ] **Step 2: Run — verify failure**

```bash
cd trust_layer && uv run pytest tests/blocks/test_guardrails.py -v
```
Expected: `ModuleNotFoundError: No module named 'trust_layer.src.blocks.guardrails'`

- [ ] **Step 3: Create `trust_layer/src/blocks/guardrails.py`**

```python
"""
trust_layer/src/blocks/guardrails.py

GuardrailsBlock — pre-LLM constraint assembly from Risk Taxonomy and Policy Packs.

Loads the active Policy Pack at construction time from config.
Maps active_risks → guardrail definitions → structured control artifacts.

Config section: trust.policy_pack (name) + trust.policy_packs.<name>
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class GuardrailsBlock:
    """
    Assembles prompt constraints, disclosures, and action gates from active risks.

    Args:
        config: Full config dict containing trust.policy_pack and trust.policy_packs.
    """

    def __init__(self, config: dict) -> None:
        trust_cfg = (config or {}).get("trust", {})
        pack_name: str = trust_cfg.get("policy_pack", "")
        all_packs: dict = trust_cfg.get("policy_packs", {})
        pack = all_packs.get(pack_name, {})

        # guardrail definitions keyed by risk ID
        self._guardrails: dict = pack.get("guardrails", {})

        logger.info(
            "guardrails_block.init",
            extra={
                "operation": "guardrails_block.init",
                "status": "success",
                "policy_pack": pack_name,
                "guardrail_count": len(self._guardrails),
            },
        )

    def assemble_constraints(
        self,
        session_id: str,
        workflow_step: str,
        active_risks: list[str],
        user_segment: str | None,
    ) -> dict:
        """
        Build guardrail control artifacts for the given active risks.

        Args:
            session_id: Current session identifier.
            workflow_step: Current subagent step (informational; not used for filtering here).
            active_risks: Risk IDs identified by NLU (e.g. ["false_certainty"]).
            user_segment: Optional user segment label (reserved for future segment-scoped rules).

        Returns:
            dict with keys:
                prompt_constraints (list[str]),
                required_disclosures (list[str]),
                action_gates (dict[str, bool]),
                refusal_templates (dict[str, str]).
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()
        prompt_constraints: list[str] = []
        required_disclosures: list[str] = []
        action_gates: dict[str, bool] = {}
        refusal_templates: dict[str, str] = {}

        for risk_id in (active_risks or []):
            guardrail = self._guardrails.get(risk_id)
            if guardrail is None:
                continue

            prompt_constraints.extend(guardrail.get("prompt_constraints", []))
            required_disclosures.extend(guardrail.get("required_disclosures", []))

            template = guardrail.get("refusal_template")
            if template:
                refusal_templates[risk_id] = template

        logger.info(
            "guardrails_block.assemble_constraints",
            extra={
                "operation": "guardrails_block.assemble_constraints",
                "status": "success",
                "session_id": session_id,
                "active_risks": active_risks,
                "constraints_count": len(prompt_constraints),
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

        return {
            "prompt_constraints": prompt_constraints,
            "required_disclosures": required_disclosures,
            "action_gates": action_gates,
            "refusal_templates": refusal_templates,
        }
```

- [ ] **Step 4: Run — verify pass**

```bash
cd trust_layer && uv run pytest tests/blocks/test_guardrails.py -v
```
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add trust_layer/src/blocks/guardrails.py trust_layer/tests/blocks/test_guardrails.py
git commit -m "feat(trust-layer): add GuardrailsBlock with Policy Pack constraint assembly"
```

---

## Task 4: ConsentBlock

**Files:**
- Create: `trust_layer/src/blocks/consent.py`
- Create: `trust_layer/tests/blocks/test_consent.py`

- [ ] **Step 1: Write the failing tests**

```python
# trust_layer/tests/blocks/test_consent.py
import pytest
from trust_layer.src.blocks.consent import ConsentBlock


@pytest.fixture
def block():
    return ConsentBlock({
        "trust": {
            "consent": {
                "consent_phrases": ["haan", "yes", "theek hai", "manzoor hai"],
                "decline_phrases": ["nahi", "no", "nahi chahiye"],
            }
        }
    })


# ── normal ────────────────────────────────────────────────────────────────
def test_consent_phrase_grants(block):
    assert block.verify_consent("s1", "haan, theek hai") is True


def test_decline_phrase_denies(block):
    assert block.verify_consent("s1", "nahi chahiye") is False


def test_yes_grants(block):
    assert block.verify_consent("s1", "yes please") is True


def test_no_denies(block):
    assert block.verify_consent("s1", "no") is False


# ── edge ──────────────────────────────────────────────────────────────────
def test_unclear_response_denies(block):
    assert block.verify_consent("s1", "mujhe samajh nahi aaya") is False


def test_empty_message_denies(block):
    assert block.verify_consent("s1", "") is False


def test_none_message_denies(block):
    assert block.verify_consent("s1", None) is False


def test_case_insensitive_match(block):
    assert block.verify_consent("s1", "HAAN bilkul") is True


def test_none_session_raises(block):
    with pytest.raises(ValueError):
        block.verify_consent(None, "haan")


# ── failure: missing config ───────────────────────────────────────────────
def test_missing_consent_config_denies():
    block = ConsentBlock({})
    assert block.verify_consent("s1", "haan") is False
```

- [ ] **Step 2: Run — verify failure**

```bash
cd trust_layer && uv run pytest tests/blocks/test_consent.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `trust_layer/src/blocks/consent.py`**

```python
"""
trust_layer/src/blocks/consent.py

ConsentBlock — DPDP Act consent phrase evaluation.

Stateless: evaluates the user's message against consent_phrases and
decline_phrases from config. Returns True if a consent phrase is found,
False for decline or unclear responses. Agent Core owns all flag management
and Memory Layer writes.

Config section: trust.consent
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class ConsentBlock:
    """
    Evaluates whether a user message grants or declines consent.

    Args:
        config: Full config dict containing trust.consent section.
    """

    def __init__(self, config: dict) -> None:
        consent_cfg = (config or {}).get("trust", {}).get("consent", {})
        self._consent_phrases: list[str] = [
            p.lower() for p in consent_cfg.get("consent_phrases", []) if p
        ]
        self._decline_phrases: list[str] = [
            p.lower() for p in consent_cfg.get("decline_phrases", []) if p
        ]

        logger.info(
            "consent_block.init",
            extra={
                "operation": "consent_block.init",
                "status": "success",
                "consent_phrase_count": len(self._consent_phrases),
                "decline_phrase_count": len(self._decline_phrases),
            },
        )

    def verify_consent(self, session_id: str, user_message: str | None) -> bool:
        """
        Evaluate user message against configured consent and decline phrases.

        Args:
            session_id: Current session identifier.
            user_message: User's response to the consent prompt.

        Returns:
            True if a consent phrase is found; False for decline or unclear.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()

        if not user_message:
            logger.info(
                "consent_block.verify",
                extra={
                    "operation": "consent_block.verify_consent",
                    "status": "success",
                    "session_id": session_id,
                    "granted": False,
                    "reason": "empty_message",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return False

        lower = user_message.lower()

        for phrase in self._consent_phrases:
            if phrase in lower:
                logger.info(
                    "consent_block.verify",
                    extra={
                        "operation": "consent_block.verify_consent",
                        "status": "success",
                        "session_id": session_id,
                        "granted": True,
                        "latency_ms": int((time.time() - start) * 1000),
                    },
                )
                return True

        logger.info(
            "consent_block.verify",
            extra={
                "operation": "consent_block.verify_consent",
                "status": "success",
                "session_id": session_id,
                "granted": False,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )
        return False
```

- [ ] **Step 4: Run — verify pass**

```bash
cd trust_layer && uv run pytest tests/blocks/test_consent.py -v
```
Expected: all 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add trust_layer/src/blocks/consent.py trust_layer/tests/blocks/test_consent.py
git commit -m "feat(trust-layer): add ConsentBlock for DPDP consent phrase evaluation"
```

---

## Task 5: HiTLBlock

**Files:**
- Create: `trust_layer/src/blocks/hitl.py`
- Create: `trust_layer/tests/blocks/test_hitl.py`

- [ ] **Step 1: Write the failing tests**

```python
# trust_layer/tests/blocks/test_hitl.py
import pytest
from trust_layer.src.blocks.hitl import HiTLBlock


@pytest.fixture
def block():
    return HiTLBlock({
        "trust": {
            "hitl": {
                "queue_backend": "log",
                "holding_message": "Aapki baat ek advisor tak pahunch rahi hai.",
                "notification_webhook": None,
            }
        }
    })


# ── normal ────────────────────────────────────────────────────────────────
def test_escalate_returns_queued(block):
    result = block.escalate(
        session_id="s1",
        escalation_reason="escalation_topic:suicide",
        user_message="main bahut pareshaan hoon",
        workflow_step="ready",
    )
    assert result["queued"] is True
    assert result["holding_message"] == "Aapki baat ek advisor tak pahunch rahi hai."
    assert result["ticket_id"].startswith("TKT-")


def test_ticket_id_unique(block):
    r1 = block.escalate("s1", "reason", "msg", "ready")
    r2 = block.escalate("s2", "reason", "msg", "ready")
    assert r1["ticket_id"] != r2["ticket_id"]


# ── edge ──────────────────────────────────────────────────────────────────
def test_empty_user_message_still_queues(block):
    result = block.escalate("s1", "reason", "", "ready")
    assert result["queued"] is True


def test_none_session_raises(block):
    with pytest.raises(ValueError):
        block.escalate(None, "reason", "msg", "ready")


# ── failure: missing config ───────────────────────────────────────────────
def test_missing_hitl_config():
    block = HiTLBlock({})
    result = block.escalate("s1", "reason", "msg", "ready")
    assert result["queued"] is True
    assert result["holding_message"] == ""
```

- [ ] **Step 2: Run — verify failure**

```bash
cd trust_layer && uv run pytest tests/blocks/test_hitl.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `trust_layer/src/blocks/hitl.py`**

```python
"""
trust_layer/src/blocks/hitl.py

HiTLBlock — Human-in-the-Loop escalation queue.

Queue backend is configurable via trust.hitl.queue_backend:
  "log"     — writes structured JSON to the Python logger (default)
  "redis"   — reserved for future implementation
  "webhook" — reserved for future implementation

Returns a ticket_id and holding_message to Agent Core. Agent Core writes
the session escalation state to Memory Layer after receiving this response.

Config section: trust.hitl
"""

from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger(__name__)


class HiTLBlock:
    """
    Submits escalation events to a configurable queue backend.

    Args:
        config: Full config dict containing trust.hitl section.
    """

    def __init__(self, config: dict) -> None:
        hitl_cfg = (config or {}).get("trust", {}).get("hitl", {})
        self._queue_backend: str = hitl_cfg.get("queue_backend", "log")
        self._holding_message: str = hitl_cfg.get("holding_message", "")
        self._notification_webhook: str | None = hitl_cfg.get("notification_webhook")

        logger.info(
            "hitl_block.init",
            extra={
                "operation": "hitl_block.init",
                "status": "success",
                "queue_backend": self._queue_backend,
            },
        )

    def escalate(
        self,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> dict:
        """
        Queue an escalation event and return a ticket ID and holding message.

        Args:
            session_id: Current session identifier.
            escalation_reason: Human-readable reason string (e.g. "escalation_topic:suicide").
            user_message: The user's message that triggered escalation.
            workflow_step: Current subagent step at time of escalation.

        Returns:
            dict with keys: queued (bool), ticket_id (str), holding_message (str).
        """
        if session_id is None:
            raise ValueError("session_id must not be None")

        start = time.time()
        ticket_id = f"TKT-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

        self._write_to_queue(
            ticket_id=ticket_id,
            session_id=session_id,
            escalation_reason=escalation_reason,
            user_message=user_message,
            workflow_step=workflow_step,
        )

        logger.info(
            "hitl_block.escalated",
            extra={
                "operation": "hitl_block.escalate",
                "status": "success",
                "session_id": session_id,
                "ticket_id": ticket_id,
                "escalation_reason": escalation_reason,
                "latency_ms": int((time.time() - start) * 1000),
            },
        )

        return {
            "queued": True,
            "ticket_id": ticket_id,
            "holding_message": self._holding_message,
        }

    def _write_to_queue(
        self,
        ticket_id: str,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> None:
        """Write escalation event to the configured queue backend."""
        if self._queue_backend == "log":
            logger.warning(
                "hitl_block.escalation_queued",
                extra={
                    "operation": "hitl_block.queue_write",
                    "status": "success",
                    "ticket_id": ticket_id,
                    "session_id": session_id,
                    "escalation_reason": escalation_reason,
                    "workflow_step": workflow_step,
                },
            )
        else:
            logger.warning(
                "hitl_block.unsupported_backend",
                extra={
                    "operation": "hitl_block.queue_write",
                    "status": "skipped",
                    "queue_backend": self._queue_backend,
                    "ticket_id": ticket_id,
                },
            )
```

- [ ] **Step 4: Run — verify pass**

```bash
cd trust_layer && uv run pytest tests/blocks/test_hitl.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add trust_layer/src/blocks/hitl.py trust_layer/tests/blocks/test_hitl.py
git commit -m "feat(trust-layer): add HiTLBlock escalation queue with log backend"
```

---

## Task 6: TrustLayer orchestrator + server update

**Files:**
- Create: `trust_layer/src/trust_layer.py`
- Modify: `trust_layer/src/server.py`
- Modify: `trust_layer/tests/test_server.py`

- [ ] **Step 1: Write the failing server tests for new endpoints**

Add these tests to `trust_layer/tests/test_server.py` (append, do not delete existing):

```python
# Append to trust_layer/tests/test_server.py

from fastapi.testclient import TestClient
# (import create_app at top if not already imported)


FULL_CONFIG = {
    "trust": {
        "policy_pack": "kkb_advisory_jobs",
        "input_rules": {
            "blocked_phrases": ["bomb"],
            "escalation_topics": ["suicide"],
            "blocked_input_message": "Cannot help.",
        },
        "output_rules": {
            "blocked_phrases": ["guaranteed placement"],
            "output_blocked_message": "Bad output.",
        },
        "policy_packs": {
            "kkb_advisory_jobs": {
                "risks": ["false_certainty"],
                "guardrails": {
                    "false_certainty": {
                        "id": "GR-001",
                        "severity": "blocker",
                        "failure_mode": "block",
                        "prompt_constraints": ["MUST NOT guarantee outcomes"],
                        "required_disclosures": ["Hiring decisions rest with employer"],
                        "refusal_template": "Main guarantee nahi de sakta.",
                    }
                },
            }
        },
        "consent": {
            "consent_phrases": ["haan", "yes"],
            "decline_phrases": ["nahi", "no"],
        },
        "hitl": {
            "queue_backend": "log",
            "holding_message": "Advisor ko connect kar rahe hain.",
            "notification_webhook": None,
        },
    }
}


def test_assemble_constraints_known_risk():
    from trust_layer.src.trust_layer import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/assemble_constraints", json={
        "session_id": "s1",
        "workflow_step": "ready",
        "active_risks": ["false_certainty"],
        "user_segment": None,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "MUST NOT guarantee outcomes" in data["prompt_constraints"]
    assert data["refusal_templates"]["false_certainty"] == "Main guarantee nahi de sakta."


def test_assemble_constraints_empty_risks():
    from trust_layer.src.trust_layer import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/assemble_constraints", json={
        "session_id": "s1",
        "workflow_step": "ready",
        "active_risks": [],
        "user_segment": None,
    })
    assert resp.status_code == 200
    assert resp.json()["prompt_constraints"] == []


def test_consent_verify_granted():
    from trust_layer.src.trust_layer import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/consent/verify", json={"session_id": "s1", "user_message": "haan"})
    assert resp.status_code == 200
    assert resp.json()["granted"] is True


def test_consent_verify_denied():
    from trust_layer.src.trust_layer import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/consent/verify", json={"session_id": "s1", "user_message": "nahi"})
    assert resp.status_code == 200
    assert resp.json()["granted"] is False


def test_escalate_returns_ticket():
    from trust_layer.src.trust_layer import TrustLayer
    tl = TrustLayer(FULL_CONFIG)
    client = TestClient(create_app(tl))
    resp = client.post("/escalate", json={
        "session_id": "s1",
        "escalation_reason": "escalation_topic:suicide",
        "user_message": "pareshaan hoon",
        "workflow_step": "ready",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] is True
    assert data["holding_message"] == "Advisor ko connect kar rahe hain."
    assert data["ticket_id"].startswith("TKT-")
```

- [ ] **Step 2: Run — verify failure**

```bash
cd trust_layer && uv run pytest tests/test_server.py -v -k "assemble_constraints or consent_verify or escalate"
```
Expected: `ImportError: cannot import name 'TrustLayer'`

- [ ] **Step 3: Create `trust_layer/src/trust_layer.py`**

```python
"""
trust_layer/src/trust_layer.py

TrustLayer — orchestrator wiring all four sub-blocks.

Replaces BasicTrustLayer as the primary implementation.
All config is parsed at construction time. Zero runtime config reads.
"""

from __future__ import annotations

import logging

from trust_layer.src.blocks.content import ContentBlock
from trust_layer.src.blocks.guardrails import GuardrailsBlock
from trust_layer.src.blocks.consent import ConsentBlock
from trust_layer.src.blocks.hitl import HiTLBlock

logger = logging.getLogger(__name__)


class TrustLayer:
    """
    Orchestrates ContentBlock, GuardrailsBlock, ConsentBlock, and HiTLBlock.

    Args:
        config: Full config dict containing a "trust" section.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        self._content = ContentBlock(config)
        self._guardrails = GuardrailsBlock(config)
        self._consent = ConsentBlock(config)
        self._hitl = HiTLBlock(config)

        logger.info(
            "trust_layer.init",
            extra={"operation": "trust_layer.init", "status": "success"},
        )

    def check_input(self, session_id: str, user_message: str, active_risks: list[str] | None = None) -> dict:
        """Delegate to ContentBlock.check_input."""
        return self._content.check_input(session_id, user_message, active_risks)

    def check_output(self, session_id: str, llm_response: str) -> dict:
        """Delegate to ContentBlock.check_output."""
        return self._content.check_output(session_id, llm_response)

    def check_consent(self, session_id: str, connector_name: str) -> bool:
        """
        Connector-level consent check. Returns True (consent assumed granted)
        until a real consent store is implemented.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        logger.info(
            "trust_layer.check_consent",
            extra={
                "operation": "trust_layer.check_consent",
                "status": "success",
                "session_id": session_id,
                "connector_name": connector_name,
            },
        )
        return True

    def assemble_constraints(
        self,
        session_id: str,
        workflow_step: str,
        active_risks: list[str],
        user_segment: str | None,
    ) -> dict:
        """Delegate to GuardrailsBlock.assemble_constraints."""
        return self._guardrails.assemble_constraints(
            session_id, workflow_step, active_risks, user_segment
        )

    def verify_consent(self, session_id: str, user_message: str) -> bool:
        """Delegate to ConsentBlock.verify_consent."""
        return self._consent.verify_consent(session_id, user_message)

    def escalate(
        self,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> dict:
        """Delegate to HiTLBlock.escalate."""
        return self._hitl.escalate(session_id, escalation_reason, user_message, workflow_step)
```

- [ ] **Step 4: Update `trust_layer/src/server.py`** — add three new endpoints

Replace the `create_app` function signature and add after the existing endpoints:

```python
# In server.py: update import at top
from trust_layer.src.trust_layer import TrustLayer
from trust_layer.src.models import (
    InputCheckRequest, OutputCheckRequest, ConsentCheckRequest,
    TrustCheckResponse, ConsentResponse, StatusResponse,
    AssembleConstraintsRequest, GuardrailConstraints,
    ConsentVerifyRequest, ConsentVerifyResponse,
    HiTLEscalateRequest, HiTLEscalateResponse,
)

# Update create_app signature to accept TrustLayer (superset of BasicTrustLayer)
def create_app(trust: TrustLayer) -> FastAPI:
    ...
    # Add after existing /check/consent endpoint:

    @app.post("/assemble_constraints")
    def assemble_constraints(request: AssembleConstraintsRequest) -> GuardrailConstraints:
        """Assemble pre-LLM guardrail constraints from active risks."""
        start = time.time()
        try:
            result = trust.assemble_constraints(
                request.session_id,
                request.workflow_step,
                request.active_risks,
                request.user_segment,
            )
            logger.info(
                "trust_server.assemble_constraints",
                extra={
                    "operation": "server.assemble_constraints",
                    "status": "success",
                    "session_id": request.session_id,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return GuardrailConstraints(**result)
        except Exception as e:
            logger.error(
                "trust_server.assemble_constraints_error",
                extra={
                    "operation": "server.assemble_constraints",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return GuardrailConstraints()   # empty constraints on error (fail-safe)

    @app.post("/consent/verify")
    def consent_verify(request: ConsentVerifyRequest) -> ConsentVerifyResponse:
        """Evaluate user message against consent phrases."""
        start = time.time()
        try:
            granted = trust.verify_consent(request.session_id, request.user_message)
            logger.info(
                "trust_server.consent_verify",
                extra={
                    "operation": "server.consent_verify",
                    "status": "success",
                    "session_id": request.session_id,
                    "granted": granted,
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ConsentVerifyResponse(granted=granted)
        except Exception as e:
            logger.error(
                "trust_server.consent_verify_error",
                extra={
                    "operation": "server.consent_verify",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return ConsentVerifyResponse(granted=False)   # fail-closed

    @app.post("/escalate")
    def escalate(request: HiTLEscalateRequest) -> HiTLEscalateResponse:
        """Submit escalation event to HiTL queue."""
        start = time.time()
        try:
            result = trust.escalate(
                request.session_id,
                request.escalation_reason,
                request.user_message,
                request.workflow_step,
            )
            logger.info(
                "trust_server.escalate",
                extra={
                    "operation": "server.escalate",
                    "status": "success",
                    "session_id": request.session_id,
                    "ticket_id": result["ticket_id"],
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return HiTLEscalateResponse(**result)
        except Exception as e:
            logger.error(
                "trust_server.escalate_error",
                extra={
                    "operation": "server.escalate",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return HiTLEscalateResponse(queued=False, ticket_id="", holding_message="")
```

- [ ] **Step 5: Run all Trust Layer tests**

```bash
cd trust_layer && uv run pytest tests/ -v
```
Expected: all existing tests pass + new server endpoint tests pass

- [ ] **Step 6: Commit**

```bash
git add trust_layer/src/trust_layer.py trust_layer/src/server.py trust_layer/tests/test_server.py
git commit -m "feat(trust-layer): wire TrustLayer orchestrator and add new server endpoints"
```

---

## Task 7: Update KKB trust_layer.yaml

**Files:**
- Modify: `dev-kit/configs/kkb/trust_layer.yaml`

- [ ] **Step 1: Replace the file contents**

```yaml
# dev-kit/configs/kkb/trust_layer.yaml
trust:
  policy_pack: "kkb_advisory_jobs"

  input_rules:
    blocked_phrases:
      - "bomb"
      - "weapon"
      - "kill"
      - "threat"
      - "violence"
    escalation_topics:
      - "suicide"
      - "arrested"
      - "police case"
      - "FIR"
      - "jail"
    blocked_input_message: "Yeh baat main handle nahi kar sakta."

  output_rules:
    blocked_phrases:
      - "guaranteed placement"
      - "100% job guarantee"
      - "as an AI, I"
    output_blocked_message: "Mujhe yeh jawab dene mein dikkat aa rahi hai."

  policy_packs:
    kkb_advisory_jobs:
      risks:
        - false_certainty
        - hallucinated_reality
        - emotional_overreach
        - dignity_harm
        - agency_violation

      guardrails:
        false_certainty:
          id: "GR-001"
          severity: "blocker"
          failure_mode: "block"
          prompt_constraints:
            - "MUST NOT guarantee or imply certainty about job outcomes"
            - "MUST frame all opportunities as possibilities, not certainties"
          required_disclosures:
            - "Hiring decisions rest with the employer"
          refusal_template: "Main kisi bhi naukri ki guarantee nahi de sakta."

        hallucinated_reality:
          id: "GR-002"
          severity: "blocker"
          failure_mode: "block"
          prompt_constraints:
            - "MUST NOT infer or generalise from missing data"
            - "MUST only reference verified signals available in context"
          required_disclosures:
            - "Information is based only on verified data"
          refusal_template: "Is baare mein koi verified data available nahi hai."

        emotional_overreach:
          id: "GR-003"
          severity: "warning"
          failure_mode: "constrain"
          prompt_constraints:
            - "MUST NOT provide counselling or motivational language"
            - "MUST limit empathetic language to one brief acknowledgement"
          required_disclosures: []
          refusal_template: null

        dignity_harm:
          id: "GR-004"
          severity: "blocker"
          failure_mode: "block"
          prompt_constraints:
            - "MUST NOT imply blame, deficiency, or judgment about the user"
          required_disclosures: []
          refusal_template: null

        agency_violation:
          id: "GR-005"
          severity: "blocker"
          failure_mode: "block"
          prompt_constraints:
            - "MUST NOT act without explicit user confirmation"
            - "MUST present options and wait for user decision before any action"
          required_disclosures:
            - "Any action requires your explicit confirmation"
          refusal_template: null

  consent:
    consent_phrases:
      - "haan"
      - "yes"
      - "theek hai"
      - "manzoor hai"
    decline_phrases:
      - "nahi"
      - "no"
      - "nahi chahiye"

  hitl:
    queue_backend: "log"
    holding_message: "Aapki baat ek advisor tak pahunch rahi hai. Thodi der mein aapse sampark hoga."
    notification_webhook: null
```

- [ ] **Step 2: Run Trust Layer tests to verify config is valid**

```bash
cd trust_layer && uv run pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add dev-kit/configs/kkb/trust_layer.yaml
git commit -m "config(kkb): add policy packs, consent phrases, and HiTL config to trust_layer.yaml"
```

---

## Task 8: Agent Core models — add `active_risks` to NLUResult

**Files:**
- Modify: `agent_core/src/models.py`
- Modify: `agent_core/tests/test_models.py` (add test) — find existing test file first

- [ ] **Step 1: Find existing NLUResult tests**

```bash
grep -rn "NLUResult" agent_core/tests/ --include="*.py" -l
```

- [ ] **Step 2: Add test for active_risks field**

In whichever test file exercises `NLUResult`, add:

```python
def test_nlu_result_active_risks_default_none():
    from agent_core.src.models import NLUResult
    result = NLUResult(intent="greeting", entities={}, sentiment="neutral", confidence=0.9)
    assert result.active_risks is None


def test_nlu_result_active_risks_set():
    from agent_core.src.models import NLUResult
    result = NLUResult(
        intent="greeting", entities={}, sentiment="neutral",
        confidence=0.9, active_risks=["false_certainty"]
    )
    assert result.active_risks == ["false_certainty"]
```

- [ ] **Step 3: Run — verify failure**

```bash
cd agent_core && uv run pytest tests/ -k "active_risks" -v
```
Expected: `TypeError: NLUResult.__init__() got an unexpected keyword argument 'active_risks'`

- [ ] **Step 4: Update `agent_core/src/models.py` — add field to NLUResult**

```python
@dataclass
class NLUResult:
    """
    Combined output of Language Normalisation and NLU Processor steps run in Agent Core.
    Produced before the Knowledge Engine call and passed as parameters to KE's retrieve().
    """

    intent: str                              # classified intent label from config intents list
    entities: dict[str, Any]                 # extracted entity key→value pairs
    sentiment: str                           # one of the configured sentiment classes
    confidence: float                        # 0.0–1.0; below threshold triggers early exit
    active_risks: list[str] | None = None    # risk signals from NLU; None if not classified
```

- [ ] **Step 5: Run — verify pass**

```bash
cd agent_core && uv run pytest tests/ -v
```
Expected: all tests PASS (new + existing)

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/models.py
git commit -m "feat(agent-core): add active_risks field to NLUResult"
```

---

## Task 9: Agent Core Trust Layer interface — add new abstract methods

**Files:**
- Modify: `agent_core/src/interfaces/trust_layer.py`

- [ ] **Step 1: Add abstract methods to `TrustLayerBase`**

```python
# agent_core/src/interfaces/trust_layer.py
"""
agent_core/interfaces/trust_layer.py

Contract that Agent Core requires from the Trust Layer DPG.
check_input() and check_output() are both mandatory on every turn.
Neither check may be skipped — this is enforced in orchestrator.py.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.models import TrustCheckResult


class TrustLayerBase(ABC):

    @abstractmethod
    def check_input(self, session_id: str, user_message: str, active_risks: Optional[list[str]] = None) -> TrustCheckResult:
        """
        Evaluate raw user input against content rules and topic firewall.
        Must be called before any LLM call.
        Returns TrustCheckResult with action "allow", "block", or "escalate".
        """

    @abstractmethod
    def check_output(self, session_id: str, llm_response: str) -> TrustCheckResult:
        """
        Evaluate LLM-generated response against output safety rules.
        Must be called before delivering any response to the user.
        Returns TrustCheckResult with action "allow", "block", or "escalate".
        """

    @abstractmethod
    def check_consent(self, session_id: str, connector_name: str) -> bool:
        """
        Verify connector-level consent for a write or identity connector.
        Returns True if consent is on record, False otherwise.
        Called by ManagerAgent before executing any write/identity tool call.
        """

    @abstractmethod
    def assemble_constraints(
        self,
        session_id: str,
        workflow_step: str,
        active_risks: list[str],
        user_segment: Optional[str],
    ) -> dict:
        """
        Assemble pre-LLM guardrail constraints from active risks.
        Returns dict with prompt_constraints, required_disclosures,
        action_gates, refusal_templates.
        """

    @abstractmethod
    def verify_consent(self, session_id: str, user_message: str) -> bool:
        """
        Evaluate user message against DPDP consent phrases.
        Returns True if consent granted, False otherwise.
        """

    @abstractmethod
    def escalate(
        self,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> dict:
        """
        Submit escalation event to HiTL queue.
        Returns dict with queued (bool), ticket_id (str), holding_message (str).
        """
```

- [ ] **Step 2: Run Agent Core tests — verify no regressions**

```bash
cd agent_core && uv run pytest tests/ -v
```
Expected: all tests PASS (the HTTP client stub will need to be updated next)

- [ ] **Step 3: Commit**

```bash
git add agent_core/src/interfaces/trust_layer.py
git commit -m "feat(agent-core): add assemble_constraints, verify_consent, escalate to TrustLayerBase"
```

---

## Task 10: Agent Core Trust Layer HTTP client — new methods + fail-closed

**Files:**
- Modify: `agent_core/src/http_clients/trust_layer.py`
- Modify: existing Trust Layer client tests (add new method tests + fail-closed tests)

- [ ] **Step 1: Find existing HTTP client tests**

```bash
grep -rn "TrustLayerHttpClient" agent_core/tests/ --include="*.py" -l
```

- [ ] **Step 2: Write failing tests for new methods and fail-closed behaviour**

In the existing Trust Layer HTTP client test file, add:

```python
import pytest
import httpx
from unittest.mock import patch, MagicMock
from agent_core.src.http_clients.trust_layer import TrustLayerHttpClient
from agent_core.src.models import TrustCheckResult

CONFIG = {"trust_client": {"endpoint": "http://localhost:8003", "timeout_ms": 2000}}


# ── assemble_constraints ──────────────────────────────────────────────────
def test_assemble_constraints_success():
    client = TrustLayerHttpClient(CONFIG)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "prompt_constraints": ["MUST NOT guarantee outcomes"],
        "required_disclosures": ["Hiring rest with employer"],
        "action_gates": {},
        "refusal_templates": {},
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.post", return_value=mock_resp):
        result = client.assemble_constraints("s1", "ready", ["false_certainty"], None)
    assert "MUST NOT guarantee outcomes" in result["prompt_constraints"]


def test_assemble_constraints_timeout_returns_empty():
    client = TrustLayerHttpClient(CONFIG)
    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        result = client.assemble_constraints("s1", "ready", ["false_certainty"], None)
    assert result["prompt_constraints"] == []


# ── verify_consent ────────────────────────────────────────────────────────
def test_verify_consent_granted():
    client = TrustLayerHttpClient(CONFIG)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"granted": True}
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.post", return_value=mock_resp):
        assert client.verify_consent("s1", "haan") is True


def test_verify_consent_timeout_returns_false():
    client = TrustLayerHttpClient(CONFIG)
    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        assert client.verify_consent("s1", "haan") is False  # fail-closed


# ── escalate ──────────────────────────────────────────────────────────────
def test_escalate_success():
    client = TrustLayerHttpClient(CONFIG)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "queued": True, "ticket_id": "TKT-001", "holding_message": "Wait..."
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.post", return_value=mock_resp):
        result = client.escalate("s1", "reason", "msg", "ready")
    assert result["queued"] is True


def test_escalate_timeout_returns_not_queued():
    client = TrustLayerHttpClient(CONFIG)
    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        result = client.escalate("s1", "reason", "msg", "ready")
    assert result["queued"] is False


# ── fail-closed: existing methods ────────────────────────────────────────
def test_check_input_timeout_returns_block():
    client = TrustLayerHttpClient(CONFIG)
    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        result = client.check_input("s1", "hello")
    assert result.action == "block"   # fail-closed, not "allow"


def test_check_output_timeout_returns_block():
    client = TrustLayerHttpClient(CONFIG)
    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        result = client.check_output("s1", "some response")
    assert result.action == "block"   # fail-closed


def test_check_consent_timeout_returns_false():
    client = TrustLayerHttpClient(CONFIG)
    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        result = client.check_consent("s1", "onest_apply")
    assert result is False   # fail-closed
```

- [ ] **Step 3: Run — verify failures (fail-closed tests will fail on existing code)**

```bash
cd agent_core && uv run pytest tests/ -k "timeout_returns_block or timeout_returns_false or assemble_constraints or verify_consent or escalate" -v
```
Expected: fail-closed tests fail (current code returns `action="allow"` and `True`)

- [ ] **Step 4: Rewrite `agent_core/src/http_clients/trust_layer.py`**

Replace entire file with this fail-closed implementation:

```python
"""
agent_core/src/http_clients/trust_layer.py

HTTP client for the Trust Layer service at port 8003.
Implements TrustLayerBase. All error handlers are fail-closed:
  check_input / check_output: return TrustCheckResult(passed=False, action="block")
  check_consent / verify_consent: return False
  escalate: return {"queued": False, "ticket_id": "", "holding_message": ""}
  assemble_constraints: return empty GuardrailConstraints dict
Never raises to the caller.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from src.interfaces.trust_layer import TrustLayerBase
from src.models import TrustCheckResult

logger = logging.getLogger(__name__)

_EMPTY_CONSTRAINTS = {
    "prompt_constraints": [],
    "required_disclosures": [],
    "action_gates": {},
    "refusal_templates": {},
}
_ESCALATE_FAILED = {"queued": False, "ticket_id": "", "holding_message": ""}


class TrustLayerHttpClient(TrustLayerBase):
    """
    HTTP client calling the Trust Layer service. Fail-closed on all errors.

    Args:
        config: Full config dict. Reads trust_client.endpoint and
                trust_client.timeout_ms.
    """

    def __init__(self, config: dict) -> None:
        if config is None:
            raise ValueError("config must not be None")
        client_cfg = config.get("trust_client", {})
        self._endpoint: str = client_cfg.get("endpoint", "http://localhost:8003")
        self._timeout_s: float = client_cfg.get("timeout_ms", 2000) / 1000
        logger.info(
            "trust_http_client.init",
            extra={
                "operation": "trust_http_client.init",
                "status": "success",
                "endpoint": self._endpoint,
            },
        )

    # ------------------------------------------------------------------
    # TrustLayerBase interface
    # ------------------------------------------------------------------

    def check_input(
        self, session_id: str, user_message: str, active_risks: Optional[list[str]] = None
    ) -> TrustCheckResult:
        """Call POST /check/input. Returns block on any failure (fail-closed)."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        try:
            resp = httpx.post(
                f"{self._endpoint}/check/input",
                json={"session_id": session_id, "message": user_message or "", "active_risks": active_risks},
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            result = TrustCheckResult(
                passed=data.get("passed", False),
                action=data.get("action", "block"),
                reason=data.get("reason"),
            )
            logger.info("trust_http_client.check_input", extra={
                "operation": "trust_http_client.check_input", "status": "success",
                "session_id": session_id, "action": result.action,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return result
        except Exception as e:
            logger.error("trust_http_client.check_input_error", extra={
                "operation": "trust_http_client.check_input", "status": "failure",
                "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            return TrustCheckResult(passed=False, action="block")  # fail-closed

    def check_output(self, session_id: str, llm_response: str) -> TrustCheckResult:
        """Call POST /check/output. Returns block on any failure (fail-closed)."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        try:
            resp = httpx.post(
                f"{self._endpoint}/check/output",
                json={"session_id": session_id, "response": llm_response or ""},
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            result = TrustCheckResult(
                passed=data.get("passed", False),
                action=data.get("action", "block"),
                reason=data.get("reason"),
            )
            logger.info("trust_http_client.check_output", extra={
                "operation": "trust_http_client.check_output", "status": "success",
                "session_id": session_id, "action": result.action,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return result
        except Exception as e:
            logger.error("trust_http_client.check_output_error", extra={
                "operation": "trust_http_client.check_output", "status": "failure",
                "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            return TrustCheckResult(passed=False, action="block")  # fail-closed

    def check_consent(self, session_id: str, connector_name: str) -> bool:
        """Call POST /check/consent. Returns False on any failure (fail-closed)."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        try:
            resp = httpx.post(
                f"{self._endpoint}/check/consent",
                json={"session_id": session_id, "connector_name": connector_name or ""},
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            granted = bool(resp.json().get("granted", False))
            logger.info("trust_http_client.check_consent", extra={
                "operation": "trust_http_client.check_consent", "status": "success",
                "session_id": session_id, "granted": granted,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return granted
        except Exception as e:
            logger.error("trust_http_client.check_consent_error", extra={
                "operation": "trust_http_client.check_consent", "status": "failure",
                "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            return False  # fail-closed

    def assemble_constraints(
        self,
        session_id: str,
        workflow_step: str,
        active_risks: list[str],
        user_segment: Optional[str],
    ) -> dict:
        """Call POST /assemble_constraints. Returns empty constraints on failure."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        try:
            resp = httpx.post(
                f"{self._endpoint}/assemble_constraints",
                json={
                    "session_id": session_id,
                    "workflow_step": workflow_step,
                    "active_risks": active_risks,
                    "user_segment": user_segment,
                },
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("trust_http_client.assemble_constraints", extra={
                "operation": "trust_http_client.assemble_constraints", "status": "success",
                "session_id": session_id,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return data
        except Exception as e:
            logger.error("trust_http_client.assemble_constraints_error", extra={
                "operation": "trust_http_client.assemble_constraints", "status": "failure",
                "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            return _EMPTY_CONSTRAINTS  # fail-safe: empty constraints

    def verify_consent(self, session_id: str, user_message: str) -> bool:
        """Call POST /consent/verify. Returns False on any failure (fail-closed)."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        try:
            resp = httpx.post(
                f"{self._endpoint}/consent/verify",
                json={"session_id": session_id, "user_message": user_message or ""},
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            granted = bool(resp.json().get("granted", False))
            logger.info("trust_http_client.verify_consent", extra={
                "operation": "trust_http_client.verify_consent", "status": "success",
                "session_id": session_id, "granted": granted,
                "latency_ms": int((time.time() - start) * 1000),
            })
            return granted
        except Exception as e:
            logger.error("trust_http_client.verify_consent_error", extra={
                "operation": "trust_http_client.verify_consent", "status": "failure",
                "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            return False  # fail-closed

    def escalate(
        self,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> dict:
        """Call POST /escalate. Returns queued=False on any failure."""
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        try:
            resp = httpx.post(
                f"{self._endpoint}/escalate",
                json={
                    "session_id": session_id,
                    "escalation_reason": escalation_reason,
                    "user_message": user_message or "",
                    "workflow_step": workflow_step,
                },
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("trust_http_client.escalate", extra={
                "operation": "trust_http_client.escalate", "status": "success",
                "session_id": session_id, "ticket_id": data.get("ticket_id"),
                "latency_ms": int((time.time() - start) * 1000),
            })
            return data
        except Exception as e:
            logger.error("trust_http_client.escalate_error", extra={
                "operation": "trust_http_client.escalate", "status": "failure",
                "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - start) * 1000),
            })
            return _ESCALATE_FAILED
```

- [ ] **Step 5: Run — verify pass**

```bash
cd agent_core && uv run pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/http_clients/trust_layer.py
git commit -m "feat(agent-core): make Trust Layer HTTP client fail-closed, add new methods"
```

---

## Task 11: Orchestrator — consent gate

**Files:**
- Modify: `agent_core/src/orchestrator.py`
- Modify: `dev-kit/dpg/agent_core.yaml`
- Add tests to existing orchestrator test file

- [ ] **Step 1: Add `ask_for_consent` to `dev-kit/dpg/agent_core.yaml`**

Find the `agent:` section in `dev-kit/dpg/agent_core.yaml` and add:

```yaml
agent:
  ask_for_consent: false
  consent_prompt: ""
```

- [ ] **Step 2: Write failing tests for consent gate**

In the existing orchestrator test file, add:

```python
# Consent gate tests — add to existing test_orchestrator.py

def _make_session_no_consent():
    """Session state: fresh user, no user_storage_mode, no prior turns."""
    return {
        "session": {"current_subagent_id": None, "turn_count": 0},
        "profile": {},
        "graph": {},
    }

def _make_session_consent_asked():
    """Session state: consent asked but not yet evaluated."""
    return {
        "session": {"current_subagent_id": None, "turn_count": 1, "user_storage_mode": None},
        "profile": {},
        "graph": {},
    }

def _make_session_consent_done():
    """Session state: consent evaluated, user_storage_mode set."""
    return {
        "session": {"current_subagent_id": "profile_building", "turn_count": 2,
                    "user_storage_mode": "saved"},
        "profile": {},
        "graph": {},
    }


def test_consent_gate_turn1_returns_prompt(mock_components):
    """Turn 1 with ask_for_consent=true and no prior turns → return consent prompt, no LLM."""
    config = {**BASE_CONFIG, "agent": {**BASE_CONFIG["agent"], "ask_for_consent": True,
              "consent_prompt": "Kya aap agree karte hain?"}}
    mock_components["memory"].context_bundle.return_value = _bundle(_make_session_no_consent())
    core = AgentCore(config=config, **mock_components)

    result = core.process_turn(TurnInput(session_id="s1", user_message="hello", channel="cli"))

    assert result.response_text == "Kya aap agree karte hain?"
    mock_components["llm"].call.assert_not_called()


def test_consent_gate_turn2_granted_proceeds(mock_components):
    """Turn 2 with user_storage_mode=None → verify consent, write memory, proceed to subagent."""
    config = {**BASE_CONFIG, "agent": {**BASE_CONFIG["agent"], "ask_for_consent": True,
              "consent_prompt": "Kya aap agree karte hain?"}}
    mock_components["memory"].context_bundle.return_value = _bundle(_make_session_consent_asked())
    mock_components["trust"].verify_consent.return_value = True
    core = AgentCore(config=config, **mock_components)

    core.process_turn(TurnInput(session_id="s1", user_message="haan", channel="cli"))

    mock_components["trust"].verify_consent.assert_called_once_with("s1", "haan")
    # Memory write should include user_storage_mode
    write_calls = mock_components["memory"].write.call_args_list
    written_data = write_calls[0][0][1] if write_calls else {}
    assert written_data.get("user_storage_mode") == "saved"


def test_consent_gate_disabled_skips_entirely(mock_components):
    """ask_for_consent=false → consent gate never entered, trust.verify_consent not called."""
    config = {**BASE_CONFIG, "agent": {**BASE_CONFIG["agent"], "ask_for_consent": False}}
    mock_components["memory"].context_bundle.return_value = _bundle(_make_session_no_consent())
    core = AgentCore(config=config, **mock_components)

    core.process_turn(TurnInput(session_id="s1", user_message="hello", channel="cli"))

    mock_components["trust"].verify_consent.assert_not_called()


def test_consent_gate_done_skips_on_subsequent_turns(mock_components):
    """user_storage_mode already set → skip consent gate."""
    config = {**BASE_CONFIG, "agent": {**BASE_CONFIG["agent"], "ask_for_consent": True,
              "consent_prompt": "Agree?"}}
    mock_components["memory"].context_bundle.return_value = _bundle(_make_session_consent_done())
    core = AgentCore(config=config, **mock_components)

    core.process_turn(TurnInput(session_id="s1", user_message="electrician ka kaam chahiye", channel="cli"))

    mock_components["trust"].verify_consent.assert_not_called()
```

- [ ] **Step 3: Run — verify failures**

```bash
cd agent_core && uv run pytest tests/ -k "consent_gate" -v
```
Expected: tests fail — consent gate not yet implemented

- [ ] **Step 4: Add consent gate to `agent_core/src/orchestrator.py`**

In `process_turn`, after Step 1 (read session state) and before Step 2 (resolve subagent), insert:

```python
        # ── Consent gate ──────────────────────────────────────────────
        ask_for_consent: bool = self._config.get("agent", {}).get("ask_for_consent", False)
        if ask_for_consent:
            user_storage_mode: str | None = bundle.session.get("user_storage_mode")
            turn_count: int = bundle.session.get("turn_count", 0)

            if user_storage_mode is None and turn_count == 0:
                # Turn 1: deliver consent prompt, no LLM call
                consent_prompt: str = self._config.get("agent", {}).get("consent_prompt", "")
                logger.info(
                    "  [CONSENT GATE] turn=1 → delivering consent prompt",
                    extra={"operation": "orchestrator.consent_gate", "status": "prompt_delivered"},
                )
                return TurnResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    response_text=consent_prompt,
                    intent="consent_prompt",
                    latency_ms=int((time.time() - start) * 1000),
                )

            if user_storage_mode is None and turn_count > 0:
                # Turn 2: evaluate response, write flags, continue to workflow
                granted: bool = self._trust.verify_consent(session_id, turn_input.user_message)
                new_storage_mode = "saved" if granted else "anonymous"
                logger.info(
                    "  [CONSENT GATE] turn=2 granted=%s → user_storage_mode=%s",
                    granted, new_storage_mode,
                )
                # Write consent result immediately (sync write for correctness)
                self._memory.write(session_id, {"user_storage_mode": new_storage_mode})
```

- [ ] **Step 5: Run — verify pass**

```bash
cd agent_core && uv run pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/orchestrator.py dev-kit/dpg/agent_core.yaml
git commit -m "feat(agent-core): add consent gate to orchestrator driven by ask_for_consent config"
```

---

## Task 12: Manager Agent — guardrail constraint injection

**Files:**
- Modify: `agent_core/src/manager_agent.py`
- Modify: orchestrator to call `/assemble_constraints` and pass result

- [ ] **Step 1: Write failing tests**

In the existing manager agent test file, add:

```python
def test_system_prompt_includes_guardrail_constraints(mock_manager_agent):
    """prompt_constraints from guardrails are appended to the system prompt."""
    constraints = {
        "prompt_constraints": ["MUST NOT guarantee outcomes"],
        "required_disclosures": ["Hiring decisions rest with employer"],
        "action_gates": {},
        "refusal_templates": {},
    }
    result = mock_manager_agent.run(
        session_id="s1",
        user_message="kya naukri milegi?",
        nlu_result=make_nlu_result(),
        context_bundle=make_bundle(),
        guardrail_constraints=constraints,
    )
    assert "MUST NOT guarantee outcomes" in result.system_prompt
    assert "Hiring decisions rest with employer" in result.system_prompt


def test_system_prompt_no_guardrails_unchanged(mock_manager_agent):
    """Empty constraints do not alter the system prompt."""
    empty = {"prompt_constraints": [], "required_disclosures": [], "action_gates": {}, "refusal_templates": {}}
    result_with = mock_manager_agent.run(..., guardrail_constraints=empty)
    result_without = mock_manager_agent.run(..., guardrail_constraints=None)
    assert result_with.system_prompt == result_without.system_prompt
```

- [ ] **Step 2: Run — verify failure**

```bash
cd agent_core && uv run pytest tests/ -k "guardrail_constraints" -v
```
Expected: `TypeError: run() got an unexpected keyword argument 'guardrail_constraints'`

- [ ] **Step 3: Update `agent_core/src/manager_agent.py`**

Find the `run` method (or equivalent prompt assembly method) and:
1. Add `guardrail_constraints: dict | None = None` parameter
2. After building the base system prompt from the subagent, append constraints:

```python
def _build_system_prompt(
    self,
    subagent_prompt: str,
    guardrail_constraints: dict | None,
) -> str:
    """Assemble system prompt from subagent prompt and optional guardrail constraints."""
    parts = [subagent_prompt]

    if guardrail_constraints:
        constraints = guardrail_constraints.get("prompt_constraints", [])
        disclosures = guardrail_constraints.get("required_disclosures", [])

        if constraints:
            parts.append("\n\n## Guardrail Constraints\n" + "\n".join(f"- {c}" for c in constraints))
        if disclosures:
            parts.append("\n\n## Required Disclosures\n" + "\n".join(f"- {d}" for d in disclosures))

    return "\n".join(parts)
```

- [ ] **Step 4: Update orchestrator to call `/assemble_constraints`**

In `process_turn`, after the input trust check passes and before the Manager Agent call, add:

```python
        # ── Assemble guardrail constraints (pre-LLM) ──────────────────
        guardrail_constraints: dict | None = None
        if nlu_result.active_risks:
            guardrail_constraints = self._trust.assemble_constraints(
                session_id=session_id,
                workflow_step=current_subagent_id,
                active_risks=nlu_result.active_risks,
                user_segment=bundle.profile.get("user_segment"),
            )
            logger.info(
                "  [GUARDRAILS] active_risks=%s → constraints=%d disclosures=%d",
                nlu_result.active_risks,
                len(guardrail_constraints.get("prompt_constraints", [])),
                len(guardrail_constraints.get("required_disclosures", [])),
            )
```

Pass `guardrail_constraints` through to the Manager Agent call.

- [ ] **Step 5: Run — verify pass**

```bash
cd agent_core && uv run pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add agent_core/src/manager_agent.py agent_core/src/orchestrator.py
git commit -m "feat(agent-core): inject guardrail constraints into system prompt via Manager Agent"
```

---

## Task 13: Remove greeting subagent + update KKB agent_core.yaml

**Files:**
- Modify: `dev-kit/configs/kkb/agent_core.yaml`

- [ ] **Step 1: Remove the greeting subagent**

In `dev-kit/configs/kkb/agent_core.yaml`:
- Delete the entire `greeting` subagent block (the one with `is_start: true` that asks for consent)
- Set `is_start: true` on the `profile_building` subagent
- Remove `session_writes` entries from any routing rules

- [ ] **Step 2: Add `ask_for_consent` and `consent_prompt` to KKB agent config**

In `dev-kit/configs/kkb/agent_core.yaml`, under the `agent:` section add:

```yaml
agent:
  ask_for_consent: true
  consent_prompt: "Namaste! Kya main aapki trade aur location yaad rakh sakta hoon? Isse agle baar aapko dobara batana nahi padega."
```

- [ ] **Step 3: Run full Agent Core test suite**

```bash
cd agent_core && uv run pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 4: Run full Trust Layer test suite**

```bash
cd trust_layer && uv run pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add dev-kit/configs/kkb/agent_core.yaml
git commit -m "config(kkb): remove greeting subagent, set profile_building as start, enable consent gate"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| ContentBlock (phrase + risk-signal checks) | Task 2 |
| GuardrailsBlock (Policy Pack → constraints) | Task 3 |
| ConsentBlock (phrase eval) | Task 4 |
| HiTLBlock (queue + holding_message) | Task 5 |
| TrustLayer orchestrator | Task 6 |
| New server endpoints (/assemble_constraints, /consent/verify, /escalate) | Task 6 |
| YAML config (policy_packs, consent, hitl) | Task 7 |
| NLUResult.active_risks | Task 8 |
| TrustLayerBase new abstract methods | Task 9 |
| HTTP client fail-closed + new methods | Task 10 |
| Orchestrator consent gate | Task 11 |
| Manager Agent guardrail injection | Task 12 |
| Remove greeting subagent | Task 13 |
| ask_for_consent in dpg/agent_core.yaml | Task 11 |
| ask_for_consent: true in kkb/agent_core.yaml | Task 13 |
| Fail-closed on all TL HTTP errors | Task 10 |
| Learning Layer TurnEvent on block/escalate | Existing orchestrator logic — no change needed |
| action_gates applied to tool list | Noted in Task 12 — manager_agent filters tool list by action_gates |
