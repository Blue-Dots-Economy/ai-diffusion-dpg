"""
agent_core/src/http_clients/trust_layer.py

HTTP client for the Trust Layer service at port 8003.
Implements TrustLayerBase. All error handlers are fail-closed:
  check_input / check_output: return TrustCheckResult(passed=False, action="block")
  check_consent / verify_consent: return False
  escalate: return {"queued": False, "ticket_id": "", "holding_message": ""}
  assemble_constraints: raises TrustLayerConstraintError on failure (fail-closed)
Never raises to the caller (except assemble_constraints which raises to block the turn).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from src.interfaces.trust_layer import TrustLayerBase
from src.models import TrustCheckResult

logger = logging.getLogger(__name__)


class TrustLayerConstraintError(RuntimeError):
    """Raised when assemble_constraints fails; caller must block the turn."""

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

    def check_input(
        self, session_id: str, user_message: str, active_risks: Optional[list[str]] = None
    ) -> TrustCheckResult:
        """Call POST /check/input. Returns block on any failure (fail-closed).

        Args:
            session_id: Active session identifier.
            user_message: Raw user input to evaluate.
            active_risks: Optional list of active risk tags from prior turn analysis.

        Returns:
            TrustCheckResult with action "allow", "block", or "escalate".

        Raises:
            ValueError: If session_id is None.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):  # 1 retry
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
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    logger.warning("trust_http_client.check_input_retry", extra={
                        "operation": "trust_http_client.check_input", "status": "retrying",
                        "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    })
                    time.sleep(0.1)
                    continue
                logger.error("trust_http_client.check_input_error", extra={
                    "operation": "trust_http_client.check_input", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return TrustCheckResult(passed=False, action="block")  # fail-closed
            except Exception as e:
                logger.error("trust_http_client.check_input_error", extra={
                    "operation": "trust_http_client.check_input", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                }, exc_info=True)
                return TrustCheckResult(passed=False, action="block")  # fail-closed
        return TrustCheckResult(passed=False, action="block")  # fail-closed (unreachable but safe)

    def check_output(self, session_id: str, llm_response: str) -> TrustCheckResult:
        """Call POST /check/output. Returns block on any failure (fail-closed).

        Args:
            session_id: Active session identifier.
            llm_response: LLM-generated response text to evaluate.

        Returns:
            TrustCheckResult with action "allow", "block", or "escalate".

        Raises:
            ValueError: If session_id is None.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):  # 1 retry
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
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    logger.warning("trust_http_client.check_output_retry", extra={
                        "operation": "trust_http_client.check_output", "status": "retrying",
                        "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    })
                    time.sleep(0.1)
                    continue
                logger.error("trust_http_client.check_output_error", extra={
                    "operation": "trust_http_client.check_output", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return TrustCheckResult(passed=False, action="block")  # fail-closed
            except Exception as e:
                logger.error("trust_http_client.check_output_error", extra={
                    "operation": "trust_http_client.check_output", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                }, exc_info=True)
                return TrustCheckResult(passed=False, action="block")  # fail-closed
        return TrustCheckResult(passed=False, action="block")  # fail-closed (unreachable but safe)

    def check_consent(self, session_id: str, connector_name: str) -> bool:
        """Call POST /check/consent. Returns False on any failure (fail-closed).

        Args:
            session_id: Active session identifier.
            connector_name: Name of the write/identity connector to check consent for.

        Returns:
            True if consent is on record, False otherwise.

        Raises:
            ValueError: If session_id is None.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):  # 1 retry
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
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    logger.warning("trust_http_client.check_consent_retry", extra={
                        "operation": "trust_http_client.check_consent", "status": "retrying",
                        "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    })
                    time.sleep(0.1)
                    continue
                logger.error("trust_http_client.check_consent_error", extra={
                    "operation": "trust_http_client.check_consent", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return False  # fail-closed
            except Exception as e:
                logger.error("trust_http_client.check_consent_error", extra={
                    "operation": "trust_http_client.check_consent", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                }, exc_info=True)
                return False  # fail-closed
        return False  # fail-closed (unreachable but safe)

    def assemble_constraints(
        self,
        session_id: str,
        workflow_step: str,
        active_risks: list[str],
        user_segment: Optional[str],
    ) -> dict:
        """Call POST /assemble_constraints. Raises TrustLayerConstraintError on failure (fail-closed).

        Args:
            session_id: Active session identifier.
            workflow_step: Current workflow step name.
            active_risks: List of active risk tags from NLU analysis.
            user_segment: Optional user segment identifier.

        Returns:
            Dict with prompt_constraints, required_disclosures, action_gates,
            and refusal_templates.

        Raises:
            ValueError: If session_id is None.
            TrustLayerConstraintError: On any failure; caller must block the turn.
        """
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
            raise TrustLayerConstraintError(f"assemble_constraints failed: {type(e).__name__}: {e}") from e

    def verify_consent(self, session_id: str, user_message: str) -> bool:
        """Call POST /consent/verify. Returns False on any failure (fail-closed).

        Args:
            session_id: Active session identifier.
            user_message: User message to evaluate for consent phrases.

        Returns:
            True if consent is granted, False otherwise.

        Raises:
            ValueError: If session_id is None.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):  # 1 retry
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
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    logger.warning("trust_http_client.verify_consent_retry", extra={
                        "operation": "trust_http_client.verify_consent", "status": "retrying",
                        "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    })
                    time.sleep(0.1)
                    continue
                logger.error("trust_http_client.verify_consent_error", extra={
                    "operation": "trust_http_client.verify_consent", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return False  # fail-closed
            except Exception as e:
                logger.error("trust_http_client.verify_consent_error", extra={
                    "operation": "trust_http_client.verify_consent", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                }, exc_info=True)
                return False  # fail-closed
        return False  # fail-closed (unreachable but safe)

    def escalate(
        self,
        session_id: str,
        escalation_reason: str,
        user_message: str,
        workflow_step: str,
    ) -> dict:
        """Call POST /escalate. Returns queued=False on any failure.

        Args:
            session_id: Active session identifier.
            escalation_reason: Reason code for the escalation.
            user_message: The user message that triggered escalation.
            workflow_step: Current workflow step at time of escalation.

        Returns:
            Dict with queued (bool), ticket_id (str), holding_message (str).
            Returns queued=False structure on failure.

        Raises:
            ValueError: If session_id is None.
        """
        if session_id is None:
            raise ValueError("session_id must not be None")
        start = time.time()
        for attempt in range(2):  # 1 retry
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
            except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
                if attempt == 0:
                    logger.warning("trust_http_client.escalate_retry", extra={
                        "operation": "trust_http_client.escalate", "status": "retrying",
                        "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    })
                    time.sleep(0.1)
                    continue
                logger.error("trust_http_client.escalate_error", extra={
                    "operation": "trust_http_client.escalate", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                })
                return dict(_ESCALATE_FAILED)
            except Exception as e:
                logger.error("trust_http_client.escalate_error", extra={
                    "operation": "trust_http_client.escalate", "status": "failure",
                    "session_id": session_id, "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                }, exc_info=True)
                return dict(_ESCALATE_FAILED)
        return dict(_ESCALATE_FAILED)  # fail-closed (unreachable but safe)
