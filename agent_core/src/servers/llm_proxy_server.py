"""
agent_core/src/servers/llm_proxy_server.py

FastAPI application exposing the internal LLM proxy endpoint.

NOTE: This server is NOT currently used. Language Normalisation and NLU Processor
call their injected ChatProviderBase directly. The proxy endpoint is retained so
that other DPG layers (e.g. Trust Layer, Action Gateway) can use it in the
future without requiring their own provider API key.

Any DPG that needs LLM access calls this endpoint instead of holding a provider
API key itself. Agent Core remains the sole owner of the key and the sole caller
of the upstream LLM API.

Endpoint:
    POST /internal/llm/call   — proxy an LLM call through ChatProviderBase
    GET  /health               — readiness probe

Request/response shape: native chat_provider neutral types (ChatRequest /
ChatResponse). The proxy is provider-agnostic — Anthropic and OpenAI both
serialise into the same neutral schema.
"""

import logging
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.chat_provider.base import ChatProviderBase
from src.chat_provider.types import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    active_model: str


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(chat_provider: ChatProviderBase) -> FastAPI:
    """Create and return the FastAPI app with the chat_provider bound to it.

    Args:
        chat_provider: The ChatProviderBase instance (typically built via
                       build_chat_provider() in main.py). Stored on app.state
                       so the endpoint can access it without globals.

    Called once at startup from main.py.
    """
    if chat_provider is None:
        raise ValueError("chat_provider must not be None")

    app = FastAPI(
        title="Agent Core — LLM Proxy",
        description="Internal LLM proxy endpoint for DPG services.",
        version="0.1.0",
        docs_url="/docs",
    )
    app.state.chat_provider = chat_provider

    # ----------------------------------------------------------------
    # POST /internal/llm/call
    # ----------------------------------------------------------------

    @app.post("/internal/llm/call", response_model=ChatResponse)
    def llm_call(request: ChatRequest) -> ChatResponse:
        """Proxy an LLM call to the configured provider.

        The caller sends a neutral ChatRequest body. Agent Core forwards
        the call (with retry, fallback, and capability validation handled
        inside the provider) and returns the ChatResponse as JSON.

        Returns HTTP 422 if messages list is empty (mirrors today's
        Pydantic-validation behaviour).
        ChatResponse with stop_reason="error" is returned in the body
        when the provider itself fails — never HTTP 500.
        """
        if not request.messages:
            raise HTTPException(status_code=422, detail="messages must not be empty")

        start = time.time()
        response: ChatResponse = app.state.chat_provider.call(request)
        latency_ms = int((time.time() - start) * 1000)
        status = "failure" if response.stop_reason == "error" else "success"

        logger.info(
            "llm_proxy.call",
            extra={
                "operation": "llm_proxy_server.llm_call",
                "status": status,
                "model": response.model_used,
                "latency_ms": latency_ms,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "stop_reason": response.stop_reason,
            },
        )

        return response

    # ----------------------------------------------------------------
    # GET /health
    # ----------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Readiness probe. Returns the currently active model name."""
        return HealthResponse(
            status="ok",
            active_model=app.state.chat_provider.get_active_model(),
        )

    return app
