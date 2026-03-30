"""
knowledge_engine/src/llm_proxy_client.py

HttpLLMWrapper — Knowledge Engine's LLM proxy client.

Makes HTTP calls to Agent Core's POST /internal/llm/call proxy endpoint.
Agent Core owns the Anthropic API key; KE never holds it.

Architecture:
    KE block → self._llm.call() → HttpLLMWrapper → HTTP POST → Agent Core → Anthropic API

NOTE: This client is currently DORMANT. It will be used by MultimodalInputHandler
(src/blocks/multimodal_input_handler.py) for image/PDF description via LLM vision
once that block is enabled (knowledge.blocks.multimodal_input_handler.enabled: true).
No other KE block (Glossary, StaticKB) calls the LLM — so llm=None is passed to
KnowledgeEngine at startup until multimodal is activated.

The proxy URL comes from YAML config (knowledge.llm_proxy_url), so switching
between local dev and production is a config-only change — zero code changes:
    Local dev:   http://localhost:8000/internal/llm/call
    Production:  http://agent-core:8000/internal/llm/call
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from src.base import LLMWrapperBase
from src.models import LLMResponse

logger = logging.getLogger(__name__)


class HttpLLMWrapper(LLMWrapperBase):
    """
    LLM client that proxies all calls through Agent Core's HTTP endpoint.

    Args:
        proxy_url:  Full URL of Agent Core's LLM proxy endpoint.
                    e.g. "http://localhost:8000/internal/llm/call"
        timeout_ms: Request timeout in milliseconds (default: 10000).
                    Must be a positive integer. Sourced from YAML config.

    Thread safety: instances are stateless after construction and safe to share.
    """

    def __init__(self, proxy_url: str, timeout_ms: int = 10000) -> None:
        if not proxy_url:
            raise ValueError("proxy_url must not be empty")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be a positive integer")

        self._proxy_url = proxy_url
        self._timeout_s = timeout_ms / 1000

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def call(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        system: str = "",
        model_override: Optional[str] = None,
    ) -> LLMResponse:
        """
        Forward an LLM call to Agent Core's proxy endpoint.

        Handles timeouts, HTTP errors, and unexpected exceptions — always
        returns an LLMResponse. Never raises to the caller.

        On any failure: LLMResponse(content=None, stop_reason="error").
        """
        if not messages:
            raise ValueError("messages must not be empty")

        payload = {
            "messages": messages,
            "tools": tools or [],
            "system": system or "",
            "model_override": model_override,
        }

        start = time.time()

        try:
            response = httpx.post(
                self._proxy_url,
                json=payload,
                timeout=self._timeout_s,
            )
            response.raise_for_status()
            data = response.json()

            result = LLMResponse(
                content=data.get("content"),
                stop_reason=data.get("stop_reason", "end_turn"),
                model_used=data.get("model_used", "proxy"),
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
            )

            logger.info(
                "llm_proxy_client.call",
                extra={
                    "operation": "llm_proxy_client.call",
                    "status": "success",
                    "latency_ms": int((time.time() - start) * 1000),
                    "model": result.model_used,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "stop_reason": result.stop_reason,
                },
            )
            return result

        except httpx.TimeoutException as e:
            logger.error(
                "llm_proxy_client.timeout",
                extra={
                    "operation": "llm_proxy_client.call",
                    "status": "failure",
                    "error": f"TimeoutException: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return LLMResponse(content=None, stop_reason="error")

        except httpx.HTTPStatusError as e:
            logger.error(
                "llm_proxy_client.http_error",
                extra={
                    "operation": "llm_proxy_client.call",
                    "status": "failure",
                    "error": f"HTTP {e.response.status_code}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return LLMResponse(content=None, stop_reason="error")

        except Exception as e:
            logger.error(
                "llm_proxy_client.unexpected_error",
                extra={
                    "operation": "llm_proxy_client.call",
                    "status": "failure",
                    "error": f"{type(e).__name__}: {e}",
                    "latency_ms": int((time.time() - start) * 1000),
                },
            )
            return LLMResponse(content=None, stop_reason="error")

    def get_active_model(self) -> str:
        """Model name is resolved by Agent Core — proxy reports 'proxy' as a placeholder."""
        return "proxy"
