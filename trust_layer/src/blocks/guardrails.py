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
        start = time.time()
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
                "latency_ms": int((time.time() - start) * 1000),
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

            guardrail_action_gates = guardrail.get("action_gates", {})
            action_gates.update(guardrail_action_gates)

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
